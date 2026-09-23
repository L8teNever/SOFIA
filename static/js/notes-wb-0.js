function createWhiteboardEngine(boardId) {
  const canvas = document.getElementById("board");
  const ctx = canvas.getContext("2d");
  const eraserCursorEl = document.getElementById("eraser-cursor");
  const statusEl = document.getElementById("status");
  const statusTextEl = document.getElementById("status-text");
  const zoomIndicatorEl = document.getElementById("zoom-indicator");
  const toolbarEl = document.getElementById("toolbar");
  const sizeSlider = document.getElementById("size-slider");
  const shapeToggleEl = document.getElementById("shape-toggle");
  const fingerDrawToggleEl = document.getElementById("finger-draw-toggle");
  const undoBtn = document.getElementById("undo-btn");
  const redoBtn = document.getElementById("redo-btn");

  const MIN_ZOOM = 0.25;
  const MAX_ZOOM = 4;
  const GRID_SIZE = 32;
  const POINTS_FLUSH_MS = 30;
  const ERASE_FLUSH_MS = 60;
  const CURSOR_SEND_MS = 45;
  const HOLD_MS = 450;
  const MIN_MOVE_WORLD = 1.2;

  const uuid = () =>
    (crypto.randomUUID && crypto.randomUUID()) ||
    "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });

  let stopped = false;

  let scale = 1;
  let offsetX = 0;
  let offsetY = 0;
  let dpr = Math.max(1, window.devicePixelRatio || 1);

  function worldToScreen(x, y) { return { x: x * scale + offsetX, y: y * scale + offsetY }; }
  function screenToWorld(x, y) { return { x: (x - offsetX) / scale, y: (y - offsetY) / scale }; }
  function clampZoom(z) { return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z)); }

  function resizeCanvas() {
    dpr = Math.max(1, window.devicePixelRatio || 1);
    canvas.width = Math.round(window.innerWidth * dpr);
    canvas.height = Math.round(window.innerHeight * dpr);
    canvas.style.width = window.innerWidth + "px";
    canvas.style.height = window.innerHeight + "px";
    requestRedraw();
  }
  window.addEventListener("resize", resizeCanvas);

  const boardStrokes = new Map();
  const remoteInProgress = new Map();
  let currentStroke = null;
  let dirty = true;
  function requestRedraw() { dirty = true; }

  function makeBBox(points) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of points) {
      if (p.x < minX) minX = p.x;
      if (p.y < minY) minY = p.y;
      if (p.x > maxX) maxX = p.x;
      if (p.y > maxY) maxY = p.y;
    }
    return { minX, minY, maxX, maxY };
  }
  function unionBBox(boxes) {
    if (boxes.length === 0) return null;
    const u = { ...boxes[0] };
    for (const b of boxes.slice(1)) {
      if (b.minX < u.minX) u.minX = b.minX;
      if (b.minY < u.minY) u.minY = b.minY;
      if (b.maxX > u.maxX) u.maxX = b.maxX;
      if (b.maxY > u.maxY) u.maxY = b.maxY;
    }
    return u;
  }

  function widthAt(size, pressure) {
    const p = pressure && pressure > 0 ? pressure : 0.5;
    return Math.max(1, size * (0.3 + 0.7 * p));
  }

  function drawStroke(stroke) {
    const pts = stroke.points;
    if (pts.length === 0) return;
    const isMarker = stroke.tool === "marker";
    ctx.globalAlpha = isMarker ? 0.35 : 1;
    if (pts.length === 1) {
      const p = pts[0];
      ctx.beginPath();
      ctx.fillStyle = stroke.color;
      ctx.arc(p.x, p.y, widthAt(stroke.size, p.p) / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
      return;
    }
    ctx.strokeStyle = stroke.color;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1];
      const b = pts[i];
      ctx.beginPath();
      ctx.lineWidth = widthAt(stroke.size, (a.p + b.p) / 2);
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
  }

  function drawGrid() {
    const topLeft = screenToWorld(0, 0);
    const bottomRight = screenToWorld(window.innerWidth, window.innerHeight);
    const startX = Math.floor(topLeft.x / GRID_SIZE) * GRID_SIZE;
    const startY = Math.floor(topLeft.y / GRID_SIZE) * GRID_SIZE;
    ctx.lineWidth = 1 / scale;
    for (let x = startX; x <= bottomRight.x; x += GRID_SIZE) {
      const bold = Math.round(x / GRID_SIZE) % 4 === 0;
      ctx.strokeStyle = bold ? "rgba(11,87,208,0.16)" : "rgba(11,87,208,0.07)";
      ctx.beginPath();
      ctx.moveTo(x, topLeft.y);
      ctx.lineTo(x, bottomRight.y);
      ctx.stroke();
    }
    for (let y = startY; y <= bottomRight.y; y += GRID_SIZE) {
      const bold = Math.round(y / GRID_SIZE) % 4 === 0;
      ctx.strokeStyle = bold ? "rgba(11,87,208,0.16)" : "rgba(11,87,208,0.07)";
      ctx.beginPath();
      ctx.moveTo(topLeft.x, y);
      ctx.lineTo(bottomRight.x, y);
      ctx.stroke();
    }
  }

  function selectionHandlePoints(b, pad) {
    const x0 = b.minX - pad, y0 = b.minY - pad, x1 = b.maxX + pad, y1 = b.maxY + pad;
    const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
    return [
      { name: "nw", x: x0, y: y0 }, { name: "n", x: mx, y: y0 }, { name: "ne", x: x1, y: y0 },
      { name: "w", x: x0, y: my }, { name: "e", x: x1, y: my },
      { name: "sw", x: x0, y: y1 }, { name: "s", x: mx, y: y1 }, { name: "se", x: x1, y: y1 },
    ];
  }
  function selectionRotateHandle(b, pad) {
    const lift = 26 / Math.max(scale, 0.25);
    return { name: "rot", x: (b.minX + b.maxX) / 2, y: b.maxY + pad + lift };
  }
  function pickScaleHandle(world, b, pad) {
    const hit = 14 / Math.max(scale, 0.25);
    for (const h of selectionHandlePoints(b, pad)) {
      if (Math.hypot(world.x - h.x, world.y - h.y) <= hit) return h.name;
    }
    return null;
  }
  function pickRotateHandle(world, b, pad) {
    const h = selectionRotateHandle(b, pad);
    return Math.hypot(world.x - h.x, world.y - h.y) <= 16 / Math.max(scale, 0.25);
  }
  function positionSelectionToolbar() {
    const bar = document.getElementById("selection-toolbar");
    if (!bar) return;
    if (!selection.ids.size || !selection.bbox) { bar.classList.add("hidden"); return; }
    bar.classList.remove("hidden");
    const b = selection.bbox;
    const s = worldToScreen((b.minX + b.maxX) / 2, b.minY);
    bar.style.left = s.x + "px";
    bar.style.top = Math.max(72, s.y - 12) + "px";
  }
  function drawLassoAndSelection() {
    if (lassoPoints && lassoPoints.length > 1) {
      ctx.save();
      ctx.setLineDash([6 / scale, 5 / scale]);
      ctx.strokeStyle = "#0b57d0";
      ctx.lineWidth = 1.5 / scale;
      ctx.beginPath();
      ctx.moveTo(lassoPoints[0].x, lassoPoints[0].y);
      for (let i = 1; i < lassoPoints.length; i++) ctx.lineTo(lassoPoints[i].x, lassoPoints[i].y);
      ctx.stroke();
      ctx.restore();
    }
    if (selection.ids.size > 0 && selection.bbox) {
      const b = selection.bbox;
      const pad = 10 / scale;
      ctx.save();
      ctx.setLineDash([6 / scale, 5 / scale]);
      ctx.strokeStyle = "#0b57d0";
      ctx.lineWidth = 1.5 / scale;
      ctx.fillStyle = "rgba(11,87,208,0.08)";
      const x = b.minX - pad, y = b.minY - pad, w = b.maxX - b.minX + pad * 2, h = b.maxY - b.minY + pad * 2;
      ctx.fillRect(x, y, w, h);
      ctx.strokeRect(x, y, w, h);
      ctx.setLineDash([]);
      const hs = 5 / scale;
      ctx.fillStyle = "#fff";
      ctx.strokeStyle = "#0b57d0";
      ctx.lineWidth = 1.4 / scale;
      for (const p of selectionHandlePoints(b, pad)) {
        ctx.fillRect(p.x - hs, p.y - hs, hs * 2, hs * 2);
        ctx.strokeRect(p.x - hs, p.y - hs, hs * 2, hs * 2);
      }
      const rot = selectionRotateHandle(b, pad);
      ctx.beginPath();
      ctx.moveTo((b.minX + b.maxX) / 2, b.maxY + pad);
      ctx.lineTo(rot.x, rot.y);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(rot.x, rot.y, 7 / scale, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }
    positionSelectionToolbar();
  }

  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#f8f9fa";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(scale * dpr, 0, 0, scale * dpr, offsetX * dpr, offsetY * dpr);
    drawGrid();
    for (const stroke of boardStrokes.values()) if (stroke.tool === "marker") drawStroke(stroke);
    for (const stroke of remoteInProgress.values()) if (stroke.tool === "marker") drawStroke(stroke);
    if (currentStroke && currentStroke.tool === "marker") drawStroke(currentStroke);
    for (const stroke of boardStrokes.values()) if (stroke.tool !== "marker") drawStroke(stroke);
    for (const stroke of remoteInProgress.values()) if (stroke.tool !== "marker") drawStroke(stroke);
    if (currentStroke && currentStroke.tool && currentStroke.tool !== "marker") drawStroke(currentStroke);
    drawLassoAndSelection();
    if (zoomIndicatorEl) zoomIndicatorEl.textContent = Math.round(scale * 100) + "%";
    repositionPresenceLabels();
  }

  function tick() {
    if (stopped || !canvas.isConnected) { stop(); return; }
    if (dirty) { draw(); dirty = false; }
    flushNetworkBuffers();
    requestAnimationFrame(tick);
  }

  let currentTool = "pen";
  let currentColor = "#1c1b1f";
  let penSize = 4;
  let markerSize = 18;
  let eraserSize = 24;
  let shapeRecognitionEnabled = true;
  let fingerDrawEnabled = false;

  function activeSize() {
    if (currentTool === "eraser") return eraserSize;
    if (currentTool === "marker") return markerSize;
    return penSize;
  }

  const toolPopover = document.getElementById("tool-popover");
  const popoverTitle = document.getElementById("popover-tool-title");
  const popoverSizeText = document.getElementById("popover-size-text");
  const settingsBackdrop = document.getElementById("settings-backdrop");
  const zoomPopover = document.getElementById("zoom-popover");
  let strokeClipboard = [];

  function setActiveSize(v) {
    const n = Number(v);
    if (currentTool === "eraser") eraserSize = n;
    else if (currentTool === "marker") markerSize = n;
    else penSize = n;
  }
  function syncSizeUI() {
    const v = activeSize();
    if (sizeSlider) sizeSlider.value = String(v);
    if (popoverSizeText) popoverSizeText.textContent = Math.round(v) + " px";
    const names = { pen: "Stift Stärke", marker: "Marker Stärke", eraser: "Radierer Größe", select: "Auswahl" };
    if (popoverTitle) popoverTitle.textContent = names[currentTool] || "Stärke";
  }
  function positionToolPopover() {
    if (!toolPopover || toolPopover.classList.contains("hidden")) return;
    const active = toolbarEl.querySelector(".tool-btn.active");
    const r = (active || toolbarEl).getBoundingClientRect();
    toolPopover.style.left = "0px";
    toolPopover.style.bottom = "calc(100% + 10px)";
  }
  function showToolPopover() {
    if (!toolPopover || currentTool === "select") { if (toolPopover) toolPopover.classList.add("hidden"); return; }
    syncSizeUI();
    toolPopover.classList.remove("hidden");
    positionToolPopover();
  }
  const toolBtnHandlers = [];
  toolbarEl.querySelectorAll(".tool-btn[data-tool]").forEach((btn) => {
    const h = (e) => {
      e.stopPropagation();
      const same = currentTool === btn.dataset.tool;
      toolbarEl.querySelectorAll(".tool-btn[data-tool]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentTool = btn.dataset.tool;
      syncSizeUI();
      updateEraserCursorVisibility();
      if (currentTool !== "select") clearSelection();
      if (same || currentTool !== "select") showToolPopover();
      else if (toolPopover) toolPopover.classList.add("hidden");
    };
    btn.addEventListener("click", h);
    toolBtnHandlers.push([btn, h]);
  });
  const swatchHandlers = [];
  toolbarEl.querySelectorAll(".swatch").forEach((btn) => {
    const h = () => {
      toolbarEl.querySelectorAll(".swatch").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentColor = btn.dataset.color;
    };
    btn.addEventListener("click", h);
    swatchHandlers.push([btn, h]);
  });
  function onSizeInput() {
    setActiveSize(sizeSlider.value);
    syncSizeUI();
    updateEraserCursorVisibility();
  }
  sizeSlider.addEventListener("input", onSizeInput);
  syncSizeUI();

  function onShapeToggle(e) {
    e.stopPropagation();
    shapeRecognitionEnabled = !shapeRecognitionEnabled;
    shapeToggleEl.classList.toggle("active", shapeRecognitionEnabled);
  }
  shapeToggleEl.addEventListener("click", onShapeToggle);

  function onFingerDrawToggle(e) {
    e.stopPropagation();
    fingerDrawEnabled = !fingerDrawEnabled;
    fingerDrawToggleEl.classList.toggle("active", fingerDrawEnabled);
  }
  fingerDrawToggleEl.addEventListener("click", onFingerDrawToggle);

  function updateEraserCursorVisibility() {
    if (currentTool !== "eraser") eraserCursorEl.style.display = "none";
  }

  function applyDock(pos) {
    toolbarEl.classList.remove("dock-bottom", "dock-top", "dock-left", "dock-right");
    toolbarEl.classList.add("dock-" + pos);
  }
  document.querySelectorAll("#notes-canvas-view .btn-dock-quick").forEach((btn) => {
    btn.addEventListener("click", (e) => { e.stopPropagation(); applyDock(btn.dataset.pos); });
  });
  const settingsToggle = document.getElementById("btn-settings-toggle");
  const settingsClose = document.getElementById("btn-settings-close");
  function hideSettings() { if (settingsBackdrop) settingsBackdrop.classList.add("hidden"); }
  function openSettings() {
    if (toolPopover) toolPopover.classList.add("hidden");
    if (zoomPopover) zoomPopover.classList.add("hidden");
    if (settingsBackdrop) settingsBackdrop.classList.remove("hidden");
  }
  if (settingsToggle) settingsToggle.addEventListener("click", (e) => { e.stopPropagation(); openSettings(); });
  if (settingsClose) settingsClose.addEventListener("click", (e) => { e.stopPropagation(); hideSettings(); });
  if (settingsBackdrop) settingsBackdrop.addEventListener("click", (e) => { if (e.target === settingsBackdrop) hideSettings(); });

  const zoomToggle = document.getElementById("btn-zoom-toggle");
  if (zoomToggle) zoomToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    if (zoomPopover) zoomPopover.classList.toggle("hidden");
  });
  function zoomAround(factor) {
    const cx = window.innerWidth / 2, cy = window.innerHeight / 2;
    const anchor = screenToWorld(cx, cy);
    scale = clampZoom(scale * factor);
    offsetX = cx - anchor.x * scale;
    offsetY = cy - anchor.y * scale;
    requestRedraw();
  }
  const zOut = document.getElementById("btn-zoom-out");
  const zIn = document.getElementById("btn-zoom-in");
  const zReset = document.getElementById("btn-zoom-reset");
  if (zOut) zOut.addEventListener("click", (e) => { e.stopPropagation(); zoomAround(1/1.2); });
  if (zIn) zIn.addEventListener("click", (e) => { e.stopPropagation(); zoomAround(1.2); });
  if (zReset) zReset.addEventListener("click", (e) => {
    e.stopPropagation();
    const cx = window.innerWidth / 2, cy = window.innerHeight / 2;
    const anchor = screenToWorld(cx, cy);
    scale = 1;
    offsetX = cx - anchor.x * scale;
    offsetY = cy - anchor.y * scale;
    requestRedraw();
  });

  const filenameEl = document.getElementById("canvas-filename");
  if (filenameEl) {
    filenameEl.addEventListener("change", async () => {
      const title = filenameEl.value.trim();
      const board = window.__notesActiveBoard;
      if (!title || !board) return;
      try {
        const updated = await API.updateNotesBoard(board.id, { title });
        window.__notesActiveBoard = updated;
        const idx = notesBoards.findIndex((x) => x.id === board.id);
        if (idx !== -1) notesBoards[idx] = updated;
      } catch { showToast("Umbenennen fehlgeschlagen"); }
    });
  }
  const shareBtn = document.getElementById("btn-share-board");
  if (shareBtn) shareBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const board = window.__notesActiveBoard;
    if (board) openEditNotesBoardSheet(board.id);
  });

  function cloneSelected() {
    const copies = [];
    for (const id of selection.ids) {
      const s = boardStrokes.get(id);
      if (s) copies.push(cloneStroke(s));
    }
    return copies;
  }
  function pasteClipboard(dx, dy) {
    if (!strokeClipboard.length) return;
    const ids = new Set();
    const boxes = [];
    for (const src of strokeClipboard) {
      const s = {
        id: uuid(), tool: src.tool, color: src.color, size: src.size,
        points: src.points.map((p) => ({ x: p.x + dx, y: p.y + dy, p: p.p })),
      };
      s.bbox = makeBBox(s.points);
      boardStrokes.set(s.id, s);
      wsSend({ type: "stroke_move", stroke: { id: s.id, tool: s.tool, color: s.color, size: s.size, points: s.points } });
      pushUndo({ type: "add", stroke: cloneStroke(s) });
      ids.add(s.id); boxes.push(s.bbox);
    }
    selection = { ids, bbox: unionBBox(boxes) };
    requestRedraw();
  }
  const copyBtn = document.getElementById("btn-sel-copy");
  const cutBtn = document.getElementById("btn-sel-cut");
  const pasteBtn = document.getElementById("btn-sel-paste");
  if (copyBtn) copyBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    strokeClipboard = cloneSelected();
    if (pasteBtn) pasteBtn.classList.toggle("hidden", !strokeClipboard.length);
  });
  if (cutBtn) cutBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    strokeClipboard = cloneSelected();
    if (pasteBtn) pasteBtn.classList.toggle("hidden", !strokeClipboard.length);
    const strokes = Array.from(selection.ids).map((id) => boardStrokes.get(id)).filter(Boolean).map(cloneStroke);
    if (strokes.length) {
      removeStrokes(strokes.map((s) => s.id));
      pushUndo({ type: "erase", strokes });
    }
    clearSelection();
  });
  if (pasteBtn) pasteBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    pasteClipboard(24, 24);
  });

  let ws = null;
  let reconnectDelay = 1000;
  let reconnectTimer = null;

  function wsSend(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  function setConnected(connected) {
    statusEl.classList.toggle("connected", connected);
    statusTextEl.textContent = connected ? "Live" : "Verbinde…";
  }

  function connectWS() {
    if (stopped) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/api/v1/notes/ws/${boardId}`);
    ws.onopen = () => { setConnected(true); reconnectDelay = 1000; };
    ws.onclose = () => {
      setConnected(false);
      if (stopped) return;
      reconnectTimer = setTimeout(connectWS, reconnectDelay);
      reconnectDelay = Math.min(10000, reconnectDelay * 1.7);
    };
    ws.onerror = () => ws.close();
    ws.onmessage = (ev) => handleMessage(JSON.parse(ev.data));
  }

  let myClientId = null;
  function finalizeIncomingStroke(id) {
    const s = remoteInProgress.get(id);
    if (!s) return;
    remoteInProgress.delete(id);
    if (s.points.length > 0) {
      s.bbox = makeBBox(s.points);
      boardStrokes.set(s.id, s);
    }
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "init": {
        myClientId = msg.clientId;
        boardStrokes.clear();
        for (const s of msg.strokes) {
          s.bbox = makeBBox(s.points);
          boardStrokes.set(s.id, s);
        }
        requestRedraw();
        break;
      }
      case "presence_join":
        ensurePresence(msg.id, msg.color);
        break;
      case "presence_leave":
        removePresence(msg.id);
        break;
      case "cursor": {
        const p = ensurePresence(msg.id, msg.color);
        p.x = msg.x; p.y = msg.y; p.tool = msg.tool; p.size = msg.size; p.lastSeen = performance.now();
        requestRedraw();
        break;
      }
      case "stroke_start":
        remoteInProgress.set(msg.strokeId, { id: msg.strokeId, tool: msg.tool, color: msg.color, size: msg.size, points: msg.points || [], ownerId: msg.id });
        requestRedraw();
        break;
      case "stroke_points": {
        const s = remoteInProgress.get(msg.strokeId);
        if (s) s.points.push(...msg.points);
        requestRedraw();
        break;
      }
      case "stroke_replace": {
        const s = remoteInProgress.get(msg.strokeId);
        if (s) s.points = msg.points;
        requestRedraw();
        break;
      }
      case "stroke_end":
        finalizeIncomingStroke(msg.strokeId);
        requestRedraw();
        break;
      case "stroke_abort":
        remoteInProgress.delete(msg.strokeId);
        requestRedraw();
        break;
      case "stroke_move": {
        const s = msg.stroke;
        if (s && s.id) { s.bbox = makeBBox(s.points); boardStrokes.set(s.id, s); requestRedraw(); }
        break;
      }
      case "erase":
        for (const id of msg.strokeIds) { boardStrokes.delete(id); remoteInProgress.delete(id); }
        requestRedraw();
        break;
    }
  }

  const presence = new Map();
  const PRESENCE_TIMEOUT_MS = 4000;
  const TOOL_LABELS = { pen: "Stift", marker: "Marker", eraser: "Radierer", select: "Auswahl" };

  function ensurePresence(id, color) {
    let p = presence.get(id);
    if (!p) {
      const el = document.createElement("div");
      el.className = "presence-label";
      (document.getElementById("notes-canvas-view") || document.body).appendChild(el);
      p = { el, color: color || "#888", x: 0, y: 0, tool: "pen", size: 4, lastSeen: performance.now() };
      presence.set(id, p);
    }
    if (color) p.color = color;
    return p;
  }
  function removePresence(id) {
    const p = presence.get(id);
    if (p) { p.el.remove(); presence.delete(id); }
  }
  function clearAllPresence() {
    for (const [id] of presence) removePresence(id);
  }
  function repositionPresenceLabels() {
    const now = performance.now();
    for (const [id, p] of presence) {
      if (now - p.lastSeen > PRESENCE_TIMEOUT_MS) { removePresence(id); continue; }
      const s = worldToScreen(p.x, p.y);
      p.el.style.left = s.x + "px";
      p.el.style.top = s.y - 14 + "px";
      p.el.style.background = p.color;
      p.el.textContent = TOOL_LABELS[p.tool] || p.tool;
    }
  }

  let lastPointsFlush = 0;
  let lastEraseFlush = 0;
  let lastCursorSend = 0;
  const pendingErase = new Set();
  let erasedThisGesture = new Set();
  let erasedStrokesThisGesture = new Map();

  function flushNetworkBuffers() {
    const now = performance.now();
    if (currentStroke && currentStroke.unsent && currentStroke.unsent.length > 0 && now - lastPointsFlush > POINTS_FLUSH_MS) {
      wsSend({ type: "stroke_points", strokeId: currentStroke.id, points: currentStroke.unsent });
      currentStroke.unsent = [];
      lastPointsFlush = now;
    }
    if (pendingErase.size > 0 && now - lastEraseFlush > ERASE_FLUSH_MS) {
      wsSend({ type: "erase", strokeIds: Array.from(pendingErase) });
      pendingErase.clear();
      lastEraseFlush = now;
    }
  }

  function sendCursor(x, y, tool, size) {
    const now = performance.now();
    if (now - lastCursorSend < CURSOR_SEND_MS) return;
    lastCursorSend = now;
    wsSend({ type: "cursor", x, y, tool, size });
  }

  const undoStack = [];
  const redoStack = [];
  const MAX_UNDO = 100;

  function cloneStroke(s) {
    return { id: s.id, tool: s.tool, color: s.color, size: s.size, points: s.points.map((p) => ({ ...p })) };
