  }
  function updateUndoRedoButtons() {
    undoBtn.disabled = undoStack.length === 0;
    redoBtn.disabled = redoStack.length === 0;
  }
  function pushUndo(action) {
    undoStack.push(action);
    if (undoStack.length > MAX_UNDO) undoStack.shift();
    redoStack.length = 0;
    updateUndoRedoButtons();
  }
  function putStroke(stroke) {
    const withBBox = { ...stroke, bbox: makeBBox(stroke.points) };
    boardStrokes.set(withBBox.id, withBBox);
    wsSend({ type: "stroke_move", stroke: { id: stroke.id, tool: stroke.tool, color: stroke.color, size: stroke.size, points: stroke.points } });
  }
  function removeStrokes(ids) {
    for (const id of ids) boardStrokes.delete(id);
    wsSend({ type: "erase", strokeIds: ids });
  }
  function applyAction(action, direction) {
    if (action.type === "add") {
      if (direction === 1) putStroke(action.stroke);
      else removeStrokes([action.stroke.id]);
    } else if (action.type === "erase") {
      if (direction === 1) removeStrokes(action.strokes.map((s) => s.id));
      else for (const s of action.strokes) putStroke(s);
    } else if (action.type === "move") {
      for (const m of action.moves) {
        const s = boardStrokes.get(m.id);
        const points = direction === 1 ? m.after : m.before;
        if (s) {
          s.points = points.map((p) => ({ ...p }));
          s.bbox = makeBBox(s.points);
          wsSend({ type: "stroke_move", stroke: { id: s.id, tool: s.tool, color: s.color, size: s.size, points: s.points } });
        }
      }
    }
    requestRedraw();
  }
  function undo() {
    if (undoStack.length === 0) return;
    const action = undoStack.pop();
    applyAction(action, -1);
    redoStack.push(action);
    updateUndoRedoButtons();
  }
  function redo() {
    if (redoStack.length === 0) return;
    const action = redoStack.pop();
    applyAction(action, 1);
    undoStack.push(action);
    updateUndoRedoButtons();
  }
  undoBtn.addEventListener("click", undo);
  redoBtn.addEventListener("click", redo);
  function onKeydownUndo(e) {
    const meta = e.ctrlKey || e.metaKey;
    if (!meta || e.key.toLowerCase() !== "z") return;
    e.preventDefault();
    if (e.shiftKey) redo(); else undo();
  }
  window.addEventListener("keydown", onKeydownUndo);
  updateUndoRedoButtons();

  let holdTimer = null;
  function clearHoldTimer() { if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; } }
  function armHoldTimer() {
    clearHoldTimer();
    if (!shapeRecognitionEnabled) return;
    if (!currentStroke || currentStroke.tool !== "pen" || currentStroke.locked) return;
    holdTimer = setTimeout(tryShapeSnap, HOLD_MS);
  }

  function perpDist(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const len2 = dx * dx + dy * dy;
    if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
    let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }
  function rdpSimplify(points, epsilon) {
    function section(pts) {
      if (pts.length < 3) return pts;
      let maxDist = 0, index = 0;
      const a = pts[0], b = pts[pts.length - 1];
      for (let i = 1; i < pts.length - 1; i++) {
        const d = perpDist(pts[i], a, b);
        if (d > maxDist) { maxDist = d; index = i; }
      }
      if (maxDist > epsilon) {
        const left = section(pts.slice(0, index + 1));
        const right = section(pts.slice(index));
        return left.slice(0, -1).concat(right);
      }
      return [a, b];
    }
    return section(points);
  }

  function detectShape(rawPoints) {
    if (rawPoints.length < 6) return null;
    const bbox = makeBBox(rawPoints);
    const w = bbox.maxX - bbox.minX, h = bbox.maxY - bbox.minY;
    const diagonal = Math.hypot(w, h);
    if (diagonal < 20) return null;
    const avgPressure = rawPoints.reduce((s, p) => s + (p.p || 0.5), 0) / rawPoints.length;
    const start = rawPoints[0], end = rawPoints[rawPoints.length - 1];
    const startEndDist = Math.hypot(end.x - start.x, end.y - start.y);
    let pathLength = 0;
    for (let i = 1; i < rawPoints.length; i++) pathLength += Math.hypot(rawPoints[i].x - rawPoints[i - 1].x, rawPoints[i].y - rawPoints[i - 1].y);
    const closed = startEndDist < diagonal * 0.3;
    if (!closed) {
      let maxDev = 0;
      for (const p of rawPoints) { const d = perpDist(p, start, end); if (d > maxDev) maxDev = d; }
      if (pathLength > 0 && maxDev / pathLength < 0.09) {
        return { type: "line", points: [{ x: start.x, y: start.y, p: avgPressure }, { x: end.x, y: end.y, p: avgPressure }] };
      }
      return null;
    }
    const cx = (bbox.minX + bbox.maxX) / 2, cy = (bbox.minY + bbox.maxY) / 2;
    const centroid = rawPoints.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }), { x: 0, y: 0 });
    centroid.x /= rawPoints.length;
    centroid.y /= rawPoints.length;
    const radii = rawPoints.map((p) => Math.hypot(p.x - centroid.x, p.y - centroid.y));
    const meanR = radii.reduce((a, b) => a + b, 0) / radii.length;
    const variance = radii.reduce((a, r) => a + (r - meanR) * (r - meanR), 0) / radii.length;
    const circleFit = meanR > 0 ? Math.sqrt(variance) / meanR : 1;
    if (circleFit < 0.2) {
      const rx = w / 2, ry = h / 2;
      const aspectDiff = Math.abs(rx - ry) / Math.max(rx, ry);
      const useCircle = aspectDiff < 0.15;
      const N = 64;
      const pts = [];
      for (let i = 0; i <= N; i++) {
        const t = (i / N) * Math.PI * 2;
        pts.push({ x: cx + (useCircle ? meanR : rx) * Math.cos(t), y: cy + (useCircle ? meanR : ry) * Math.sin(t), p: avgPressure });
      }
      return { type: "circle", points: pts };
    }
    const simplified = rdpSimplify(rawPoints, diagonal * 0.06);
    const corners = simplified.slice(0, simplified.length - 1);
    if (corners.length === 3) {
      return { type: "triangle", points: [...corners, corners[0]].map((p) => ({ x: p.x, y: p.y, p: avgPressure })) };
    }
    if (corners.length === 4) {
      const nearBBox = corners.every((p) => {
        const dCorner = Math.min(
          Math.hypot(p.x - bbox.minX, p.y - bbox.minY), Math.hypot(p.x - bbox.maxX, p.y - bbox.minY),
          Math.hypot(p.x - bbox.maxX, p.y - bbox.maxY), Math.hypot(p.x - bbox.minX, p.y - bbox.maxY)
        );
        return dCorner < diagonal * 0.12;
      });
      const quad = nearBBox
        ? [{ x: bbox.minX, y: bbox.minY }, { x: bbox.maxX, y: bbox.minY }, { x: bbox.maxX, y: bbox.maxY }, { x: bbox.minX, y: bbox.maxY }]
        : corners;
      return { type: "rectangle", points: [...quad, quad[0]].map((p) => ({ x: p.x, y: p.y, p: avgPressure })) };
    }
    return null;
  }

  function tryShapeSnap() {
    holdTimer = null;
    if (!currentStroke || currentStroke.tool !== "pen" || currentStroke.locked) return;
    const detected = detectShape(currentStroke.points);
    if (!detected) return;
    currentStroke.points = detected.points;
    currentStroke.unsent = [];
    currentStroke.locked = true;
    wsSend({ type: "stroke_replace", strokeId: currentStroke.id, points: detected.points });
    requestRedraw();
  }

  const activePointers = new Map();
  const touchPointers = new Map();
  let pinchState = null;
  let panState = null;
  let spacePressed = false;
  let lassoPoints = null;
  let lassoPointerId = null;
  let selection = { ids: new Set(), bbox: null };
  let dragState = null;

  function clearSelection() {
    selection = { ids: new Set(), bbox: null };
    lassoPoints = null;
    lassoPointerId = null;
    dragState = null;
    requestRedraw();
  }

  function pointInPolygon(pt, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i].x, yi = poly[i].y, xj = poly[j].x, yj = poly[j].y;
      const intersect = yi > pt.y !== yj > pt.y && pt.x < ((xj - xi) * (pt.y - yi)) / (yj - yi) + xi;
      if (intersect) inside = !inside;
    }
    return inside;
  }
  function pointInBBox(pt, b, pad) {
    return pt.x >= b.minX - pad && pt.x <= b.maxX + pad && pt.y >= b.minY - pad && pt.y <= b.maxY + pad;
  }

  function finalizeLasso() {
    if (!lassoPoints || lassoPoints.length < 3) { clearSelection(); lassoPoints = null; return; }
    const poly = lassoPoints;
    const ids = new Set();
    for (const stroke of boardStrokes.values()) {
      for (const p of stroke.points) {
        if (pointInPolygon(p, poly)) { ids.add(stroke.id); break; }
      }
    }
    lassoPoints = null;
    if (ids.size === 0) { clearSelection(); return; }
    selection = { ids, bbox: unionBBox(Array.from(ids).map((id) => boardStrokes.get(id).bbox)) };
    requestRedraw();
  }

  function startSelectionDrag(pointerId, world) {
    const snapshot = new Map();
    for (const id of selection.ids) {
      const s = boardStrokes.get(id);
      if (s) snapshot.set(id, s.points.map((p) => ({ x: p.x, y: p.y, p: p.p })));
    }
    dragState = { pointerId, startWorld: world, snapshot };
  }

  function updateSelectionDrag(world) {
    const dx = world.x - dragState.startWorld.x;
    const dy = world.y - dragState.startWorld.y;
    const boxes = [];
    for (const [id, pts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      s.points = pts.map((p) => ({ x: p.x + dx, y: p.y + dy, p: p.p }));
      s.bbox = makeBBox(s.points);
      boxes.push(s.bbox);
    }
    selection.bbox = unionBBox(boxes);
    requestRedraw();
  }

  function finalizeSelectionDrag() {
    const moves = [];
    for (const [id, beforePts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (s) {
        wsSend({ type: "stroke_move", stroke: { id: s.id, tool: s.tool, color: s.color, size: s.size, points: s.points } });
        moves.push({ id, before: beforePts, after: s.points.map((p) => ({ ...p })) });
      }
    }
    if (moves.length > 0) pushUndo({ type: "move", moves });
    dragState = null;
  }
  function cancelSelectionDrag() {
    for (const [id, pts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (s) { s.points = pts; s.bbox = makeBBox(pts); }
    }
    dragState = null;
    requestRedraw();
  }

  function onKeydownSpace(e) { if (e.code === "Space") spacePressed = true; }
  function onKeyupSpace(e) { if (e.code === "Space") spacePressed = false; }
  window.addEventListener("keydown", onKeydownSpace);
  window.addEventListener("keyup", onKeyupSpace);

  function pointerPressure(e) {
    if (e.pointerType === "pen") return e.pressure > 0 ? e.pressure : 0.5;
    if (e.pointerType === "mouse") return 1;
    return e.pressure > 0 ? e.pressure : 0.5;
  }

  function startStroke(pointerId, pointerType, wx, wy, pressure) {
    const id = uuid();
    const tool = currentTool === "marker" ? "marker" : "pen";
    const size = activeSize();
    currentStroke = { id, tool, color: currentColor, size, points: [{ x: wx, y: wy, p: pressure }], unsent: [], pointerId, pointerType, locked: false };
    wsSend({ type: "stroke_start", strokeId: id, tool, color: currentColor, size, points: currentStroke.points });
    if (tool === "pen") armHoldTimer();
    requestRedraw();
  }

  function extendStroke(wx, wy, pressure) {
    if (!currentStroke || currentStroke.locked) return;
    const last = currentStroke.points[currentStroke.points.length - 1];
    const moved = !last || Math.hypot(wx - last.x, wy - last.y) >= MIN_MOVE_WORLD;
    if (!moved) return;
    const point = { x: wx, y: wy, p: pressure };
    currentStroke.points.push(point);
    currentStroke.unsent.push(point);
    if (currentStroke.tool === "pen") armHoldTimer();
    requestRedraw();
  }

  function endStroke() {
    if (!currentStroke) return;
    clearHoldTimer();
    if (currentStroke.unsent.length > 0) {
      wsSend({ type: "stroke_points", strokeId: currentStroke.id, points: currentStroke.unsent });
      currentStroke.unsent = [];
    }
    wsSend({ type: "stroke_end", strokeId: currentStroke.id });
    currentStroke.bbox = makeBBox(currentStroke.points);
    boardStrokes.set(currentStroke.id, currentStroke);
    pushUndo({ type: "add", stroke: cloneStroke(currentStroke) });
    currentStroke = null;
    requestRedraw();
  }

  function abortStroke() {
    if (!currentStroke) return;
    clearHoldTimer();
    wsSend({ type: "stroke_abort", strokeId: currentStroke.id });
    currentStroke = null;
    requestRedraw();
  }

  function eraseSegment(x0, y0, x1, y1) {
    const r = eraserSize / 2;
    const dist = Math.hypot(x1 - x0, y1 - y0);
    const steps = Math.max(1, Math.ceil(dist / Math.max(4, r * 0.5)));
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const sx = x0 + (x1 - x0) * t;
      const sy = y0 + (y1 - y0) * t;
      for (const stroke of boardStrokes.values()) {
        if (erasedThisGesture.has(stroke.id)) continue;
        const b = stroke.bbox;
        if (sx < b.minX - r || sx > b.maxX + r || sy < b.minY - r || sy > b.maxY + r) continue;
        const hitR = r + stroke.size / 2;
        for (const p of stroke.points) {
          const dx = p.x - sx, dy = p.y - sy;
          if (dx * dx + dy * dy <= hitR * hitR) {
            erasedThisGesture.add(stroke.id);
            erasedStrokesThisGesture.set(stroke.id, cloneStroke(stroke));
            break;
          }
        }
      }
    }
    if (erasedThisGesture.size > 0) {
      for (const id of erasedThisGesture) { boardStrokes.delete(id); pendingErase.add(id); }
      requestRedraw();
    }
  }

  function updateEraserCursor(clientX, clientY) {
    eraserCursorEl.style.display = "block";
    eraserCursorEl.style.left = clientX + "px";
    eraserCursorEl.style.top = clientY + "px";
    const diameterScreen = eraserSize * scale;
    eraserCursorEl.style.width = diameterScreen + "px";
    eraserCursorEl.style.height = diameterScreen + "px";
  }

  function distance(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
  function midpoint(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }

  function startSelectionScale(pointerId, world, handle) {
    const snapshot = new Map();
    for (const id of selection.ids) {
      const s = boardStrokes.get(id);
      if (s) snapshot.set(id, s.points.map((p) => ({ x: p.x, y: p.y, p: p.p })));
    }
    dragState = { pointerId, kind: "scale", handle, startWorld: world, bbox: { ...selection.bbox }, snapshot };
  }
  function startSelectionRotate(pointerId, world) {
    const snapshot = new Map();
    for (const id of selection.ids) {
      const s = boardStrokes.get(id);
      if (s) snapshot.set(id, s.points.map((p) => ({ x: p.x, y: p.y, p: p.p })));
    }
    const b = selection.bbox;
    const cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2;
    dragState = { pointerId, kind: "rotate", startWorld: world, cx, cy, startAng: Math.atan2(world.y - cy, world.x - cx), snapshot };
  }
  function updateSelectionScale(world) {
    const b = dragState.bbox;
    const cx = (b.minX + b.maxX) / 2, cy = (b.minY + b.maxY) / 2;
    let sx = 1, sy = 1;
    const h = dragState.handle;
    if (h.includes("e")) sx = (world.x - b.minX) / Math.max(8, b.maxX - b.minX);
    if (h.includes("w")) sx = (b.maxX - world.x) / Math.max(8, b.maxX - b.minX);
    if (h.includes("s")) sy = (world.y - b.minY) / Math.max(8, b.maxY - b.minY);
    if (h.includes("n")) sy = (b.maxY - world.y) / Math.max(8, b.maxY - b.minY);
    if (h === "n" || h === "s") sx = sy;
    if (h === "e" || h === "w") sy = sx;
    sx = Math.max(0.08, Math.min(8, Math.abs(sx))) * Math.sign(sx || 1);
    sy = Math.max(0.08, Math.min(8, Math.abs(sy))) * Math.sign(sy || 1);
    const boxes = [];
    for (const [id, pts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      s.points = pts.map((p) => ({ x: cx + (p.x - cx) * sx, y: cy + (p.y - cy) * sy, p: p.p }));
      s.bbox = makeBBox(s.points);
      boxes.push(s.bbox);
    }
    selection.bbox = unionBBox(boxes);
    requestRedraw();
  }
  function updateSelectionRotate(world) {
    const ang = Math.atan2(world.y - dragState.cy, world.x - dragState.cx) - dragState.startAng;
    const c = Math.cos(ang), s = Math.sin(ang);
    const boxes = [];
    for (const [id, pts] of dragState.snapshot) {
      const st = boardStrokes.get(id);
      if (!st) continue;
      st.points = pts.map((p) => {
        const dx = p.x - dragState.cx, dy = p.y - dragState.cy;
        return { x: dragState.cx + dx * c - dy * s, y: dragState.cy + dx * s + dy * c, p: p.p };
      });
      st.bbox = makeBBox(st.points);
      boxes.push(st.bbox);
    }
    selection.bbox = unionBBox(boxes);
    requestRedraw();
  }

  function dispatchPrimaryDown(e) {
    const world = screenToWorld(e.clientX, e.clientY);
    if (currentTool === "select" || (selection.bbox && selection.ids.size)) {
      if (selection.bbox) {
        const pad = 10 / scale;
        const handle = pickScaleHandle(world, selection.bbox, pad);
        if (handle) { startSelectionScale(e.pointerId, world, handle); sendCursor(world.x, world.y, currentTool, activeSize()); return; }
        if (pickRotateHandle(world, selection.bbox, pad)) { startSelectionRotate(e.pointerId, world); sendCursor(world.x, world.y, currentTool, activeSize()); return; }
      }
    }
    if (currentTool === "select") {
      if (selection.bbox && pointInBBox(world, selection.bbox, 10 / scale)) {
        startSelectionDrag(e.pointerId, world);
      } else {
        clearSelection();
        lassoPointerId = e.pointerId;
        lassoPoints = [world];
      }
    } else if (currentTool === "eraser") {
      erasedThisGesture.clear();
      erasedStrokesThisGesture.clear();
      currentStroke = { pointerId: e.pointerId, eraser: true, lastX: world.x, lastY: world.y };
      eraseSegment(world.x, world.y, world.x, world.y);
      updateEraserCursor(e.clientX, e.clientY);
    } else {
      startStroke(e.pointerId, e.pointerType, world.x, world.y, pointerPressure(e));
    }
    sendCursor(world.x, world.y, currentTool, activeSize());
  }

  function onPointerDown(e) {
    try { canvas.setPointerCapture(e.pointerId); } catch (err) {}
    activePointers.set(e.pointerId, { type: e.pointerType, x: e.clientX, y: e.clientY });
    if (e.pointerType === "touch") {
      touchPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touchPointers.size === 2) {
        if (currentStroke) abortStroke();
        if (dragState) cancelSelectionDrag();
        lassoPoints = null;
        lassoPointerId = null;
        erasedThisGesture.clear();
        panState = null;
        const pts = Array.from(touchPointers.values());
        const mid = midpoint(pts[0], pts[1]);
        pinchState = { initialDist: distance(pts[0], pts[1]), initialScale: scale, anchorWorld: screenToWorld(mid.x, mid.y) };
        return;
      }
      if (touchPointers.size === 1) {
        if (fingerDrawEnabled && !pinchState) { dispatchPrimaryDown(e); return; }
        if (!pinchState) panState = { lastX: e.clientX, lastY: e.clientY };
      }
      return;
    }
    if (e.pointerType === "mouse" && (spacePressed || e.button === 1)) {
      panState = { lastX: e.clientX, lastY: e.clientY, pointerId: e.pointerId };
      return;
    }
    if (e.pointerType === "mouse" && e.button !== 0) return;
    dispatchPrimaryDown(e);
  }
  canvas.addEventListener("pointerdown", onPointerDown);

  function onPointerMove(e) {
    activePointers.set(e.pointerId, { type: e.pointerType, x: e.clientX, y: e.clientY });
    if (e.pointerType === "touch") {
      touchPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinchState && touchPointers.size === 2) {
        const pts = Array.from(touchPointers.values());
        const mid = midpoint(pts[0], pts[1]);
        const dist = distance(pts[0], pts[1]);
        const newScale = clampZoom(pinchState.initialScale * (dist / Math.max(1, pinchState.initialDist)));
        scale = newScale;
        offsetX = mid.x - pinchState.anchorWorld.x * scale;
        offsetY = mid.y - pinchState.anchorWorld.y * scale;
        requestRedraw();
        return;
      }
      const isActiveDrawTouch =
        (dragState && dragState.pointerId === e.pointerId) ||
        lassoPointerId === e.pointerId ||
        (currentStroke && currentStroke.pointerId === e.pointerId);
      if (!isActiveDrawTouch) {
        if (panState && touchPointers.size === 1) {
          offsetX += e.clientX - panState.lastX;
          offsetY += e.clientY - panState.lastY;
          panState.lastX = e.clientX;
          panState.lastY = e.clientY;
          requestRedraw();
        }
        return;
      }
    } else if (panState && (panState.pointerId === undefined || panState.pointerId === e.pointerId)) {
      offsetX += e.clientX - panState.lastX;
      offsetY += e.clientY - panState.lastY;
      panState.lastX = e.clientX;
      panState.lastY = e.clientY;
      requestRedraw();
      return;
    }
    const world = screenToWorld(e.clientX, e.clientY);
    if (currentTool === "eraser") updateEraserCursor(e.clientX, e.clientY);
    if (dragState && dragState.pointerId === e.pointerId) {
      if (touchPointers.size >= 2) { cancelSelectionDrag(); return; }
      if (dragState.kind === "scale") updateSelectionScale(world);
      else if (dragState.kind === "rotate") updateSelectionRotate(world);
      else updateSelectionDrag(world);
    } else if (lassoPointerId === e.pointerId && lassoPoints) {
      if (touchPointers.size >= 2) { lassoPoints = null; lassoPointerId = null; return; }
      lassoPoints.push(world);
      requestRedraw();
    } else if (currentStroke && currentStroke.pointerId === e.pointerId) {
      if (touchPointers.size >= 2) {
        if (currentStroke.eraser) currentStroke = null;
        else abortStroke();
        return;
      }
      if (currentStroke.eraser) {
        eraseSegment(currentStroke.lastX, currentStroke.lastY, world.x, world.y);
        currentStroke.lastX = world.x;
        currentStroke.lastY = world.y;
      } else {
        extendStroke(world.x, world.y, pointerPressure(e));
      }
    }
    sendCursor(world.x, world.y, currentTool, activeSize());
  }
  canvas.addEventListener("pointermove", onPointerMove);

  function endPointer(e) {
    activePointers.delete(e.pointerId);
    if (e.pointerType === "touch") {
      touchPointers.delete(e.pointerId);
      if (touchPointers.size < 2) pinchState = null;
      if (touchPointers.size === 0) panState = null;
      const wasActiveDrawTouch =
        (dragState && dragState.pointerId === e.pointerId) ||
        lassoPointerId === e.pointerId ||
        (currentStroke && currentStroke.pointerId === e.pointerId);
      if (!wasActiveDrawTouch) return;
    } else if (panState && (panState.pointerId === undefined || panState.pointerId === e.pointerId)) {
      panState = null;
      return;
    }
    if (dragState && dragState.pointerId === e.pointerId) { finalizeSelectionDrag(); return; }
    if (lassoPointerId === e.pointerId) { lassoPointerId = null; finalizeLasso(); return; }
    if (currentStroke && currentStroke.pointerId === e.pointerId) {
      if (currentStroke.eraser) {
        if (pendingErase.size > 0) { wsSend({ type: "erase", strokeIds: Array.from(pendingErase) }); pendingErase.clear(); }
        if (erasedStrokesThisGesture.size > 0) {
          pushUndo({ type: "erase", strokes: Array.from(erasedStrokesThisGesture.values()) });
          erasedStrokesThisGesture.clear();
        }
        currentStroke = null;
      } else {
        endStroke();
      }
    }
    if (currentTool === "eraser") eraserCursorEl.style.display = "none";
  }
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);
  function onPointerLeave(e) {
    if (currentTool === "eraser" && !activePointers.has(e.pointerId)) eraserCursorEl.style.display = "none";
  }
  canvas.addEventListener("pointerleave", onPointerLeave);

  function onWheel(e) {
    e.preventDefault();
    const anchor = screenToWorld(e.clientX, e.clientY);
    const factor = Math.exp(-e.deltaY * 0.0015);
    scale = clampZoom(scale * factor);
    offsetX = e.clientX - anchor.x * scale;
    offsetY = e.clientY - anchor.y * scale;
    requestRedraw();
  }
  canvas.addEventListener("wheel", onWheel, { passive: false });

  function onContextMenu(e) { e.preventDefault(); }
  canvas.addEventListener("contextmenu", onContextMenu);

  function stop() {
    if (stopped) return;
    stopped = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (ws) { ws.onclose = null; ws.close(); }
    clearAllPresence();
    window.removeEventListener("resize", resizeCanvas);
    window.removeEventListener("keydown", onKeydownUndo);
    window.removeEventListener("keydown", onKeydownSpace);
    window.removeEventListener("keyup", onKeyupSpace);
    hideSettings();
    const bar = document.getElementById("selection-toolbar");
    if (bar) bar.classList.add("hidden");
  }

  resizeCanvas();
  offsetX = window.innerWidth / 2;
  offsetY = window.innerHeight / 2;
  connectWS();
  requestAnimationFrame(tick);

  return { stop };
}
