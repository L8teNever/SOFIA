
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
    if (cropState && cropState.full) {
      const full = cropState.full, crop = cropState.crop;
      const cx = full.minX + crop.l * full.w, cy = full.minY + crop.t * full.h;
      const cw = (crop.r - crop.l) * full.w, ch = (crop.b - crop.t) * full.h;
      ctx.save();
      ctx.strokeStyle = "#0b57d0";
      ctx.lineWidth = 1.5 / scale;
      ctx.setLineDash([6 / scale, 5 / scale]);
      ctx.strokeRect(cx, cy, cw, ch);
      ctx.setLineDash([]);
      const hs = 5 / scale;
      ctx.fillStyle = "#fff";
      for (const p of cropHandlePoints(full, crop)) {
        ctx.fillRect(p.x - hs, p.y - hs, hs * 2, hs * 2);
        ctx.strokeRect(p.x - hs, p.y - hs, hs * 2, hs * 2);
      }
      ctx.restore();
      positionSelectionToolbar();
      return;
    }
    if (selection.ids.size > 0 && selection.bbox) {
      const b = selection.bbox, pad = 10 / scale;
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
      if (selection.ids.size === 1) {
        const st = boardStrokes.get(Array.from(selection.ids)[0]);
        const knots = st ? shapeEditKnots(st) : null;
        if (knots) {
          ctx.fillStyle = "#fff";
          for (const k of knots) {
            ctx.beginPath();
            ctx.arc(k.x, k.y, 4.5 / scale, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
          }
        }
      }
      ctx.restore();
    }
    positionSelectionToolbar();
  }
  function dispatchPrimaryDown(e) {
    const world = screenToWorld(e.clientX, e.clientY);
    if (currentTool !== "eraser" && currentTool !== "select") {
      const shaped = pickShapedStrokeAt(world);
      if (shaped) {
        beginStrokeInteraction(e.pointerId, world, shaped);
        sendCursor(world.x, world.y, currentTool, activeSize());
        return;
      }
    }
    if (currentTool === "select" || cropState) {
      if (cropState) {
        const handle = pickCropHandle(world);
        if (handle) {
          cropState.pointerId = e.pointerId; cropState.handle = handle;
          cropState.startCrop = { ...cropState.crop }; cropState.startWorld = world;
          return;
        }
        const full = cropState.full, crop = cropState.crop;
        const rect = { minX: full.minX + crop.l * full.w, minY: full.minY + crop.t * full.h, maxX: full.minX + crop.r * full.w, maxY: full.minY + crop.b * full.h };
        if (pointInBBox(world, rect, 0)) {
          cropState.pointerId = e.pointerId; cropState.handle = "move";
          cropState.startCrop = { ...cropState.crop }; cropState.startWorld = world;
          return;
        }
      }
      if (selection.bbox && !cropState) {
        const pad = 10 / scale;
        const knot = pickEditKnot(world);
        if (knot) { startPointEdit(e.pointerId, world, knot); return; }
        const handle = pickScaleHandle(world, selection.bbox, pad);
        if (handle) { startSelectionScale(e.pointerId, world, handle); return; }
        if (pickRotateHandle(world, selection.bbox, pad)) { startSelectionRotate(e.pointerId, world); return; }
      }
    }
    if (currentTool === "select") {
      if (selection.bbox && pointInBBox(world, selection.bbox, 10 / scale)) startSelectionDrag(e.pointerId, world);
      else { clearSelection(); lassoPointerId = e.pointerId; lassoPoints = [world]; }
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
  function onPointerMove(e) {
    activePointers.set(e.pointerId, { type: e.pointerType, x: e.clientX, y: e.clientY });
    if (e.pointerType === "touch") {
      touchPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinchState && touchPointers.size === 2) {
        const pts = Array.from(touchPointers.values());
        const mid = midpoint(pts[0], pts[1]);
        const dist = distance(pts[0], pts[1]);
        scale = clampZoom(pinchState.initialScale * (dist / Math.max(1, pinchState.initialDist)));
        offsetX = mid.x - pinchState.anchorWorld.x * scale;
        offsetY = mid.y - pinchState.anchorWorld.y * scale;
        requestRedraw();
        return;
      }
      const isActive =
        (dragState && dragState.pointerId === e.pointerId) ||
        (pendingShapeDrag && pendingShapeDrag.pointerId === e.pointerId) ||
        (cropState && cropState.pointerId === e.pointerId) ||
        lassoPointerId === e.pointerId ||
        (currentStroke && currentStroke.pointerId === e.pointerId);
      if (!isActive) {
        if (panState && touchPointers.size === 1) {
          offsetX += e.clientX - panState.lastX; offsetY += e.clientY - panState.lastY;
          panState.lastX = e.clientX; panState.lastY = e.clientY;
          requestRedraw();
        }
        return;
      }
    } else if (panState && (panState.pointerId === undefined || panState.pointerId === e.pointerId)) {
      offsetX += e.clientX - panState.lastX; offsetY += e.clientY - panState.lastY;
      panState.lastX = e.clientX; panState.lastY = e.clientY;
      requestRedraw();
      return;
    }
    const world = screenToWorld(e.clientX, e.clientY);
    if (currentTool === "eraser") updateEraserCursor(e.clientX, e.clientY);
    if (cropState && cropState.pointerId === e.pointerId) { updateCropDrag(world); return; }
    if (pendingShapeDrag && pendingShapeDrag.pointerId === e.pointerId) {
      if (Math.hypot(world.x - pendingShapeDrag.startWorld.x, world.y - pendingShapeDrag.startWorld.y) > 4 / Math.max(scale, 0.25)) {
        startSelectionDrag(pendingShapeDrag.pointerId, pendingShapeDrag.startWorld);
        pendingShapeDrag = null;
        updateSelectionDrag(world);
      }
      return;
    }
    if (dragState && dragState.pointerId === e.pointerId) {
      if (touchPointers.size >= 2) { cancelSelectionDrag(); return; }
      if (dragState.kind === "scale") updateSelectionScale(world);
      else if (dragState.kind === "rotate") updateSelectionRotate(world);
      else if (dragState.kind === "point") updatePointEdit(world);
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
        currentStroke.lastX = world.x; currentStroke.lastY = world.y;
      } else extendStroke(world.x, world.y, pointerPressure(e));
    }
    sendCursor(world.x, world.y, currentTool, activeSize());
  }
  function endPointer(e) {
    activePointers.delete(e.pointerId);
    if (e.pointerType === "touch") {
      touchPointers.delete(e.pointerId);
      if (touchPointers.size < 2) pinchState = null;
      if (touchPointers.size === 0) panState = null;
      const wasActive =
        (dragState && dragState.pointerId === e.pointerId) ||
        (pendingShapeDrag && pendingShapeDrag.pointerId === e.pointerId) ||
        (cropState && cropState.pointerId === e.pointerId) ||
        lassoPointerId === e.pointerId ||
        (currentStroke && currentStroke.pointerId === e.pointerId);
      if (!wasActive) return;
    } else if (panState && (panState.pointerId === undefined || panState.pointerId === e.pointerId)) {
      panState = null;
      return;
    }
    if (cropState && cropState.pointerId === e.pointerId) { cropState.pointerId = null; cropState.handle = null; return; }
    if (pendingShapeDrag && pendingShapeDrag.pointerId === e.pointerId) { pendingShapeDrag = null; return; }
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
      } else endStroke();
    }
    if (currentTool === "eraser") eraserCursorEl.style.display = "none";
  }

  function pasteClipboard(dx, dy) {
    if (!strokeClipboard.length) return;
    const ids = new Set(), boxes = [];
    for (const src of strokeClipboard) {
      const s = {
        id: uuid(), tool: src.tool, color: src.color, size: src.size,
        points: src.points.map((p) => ({ x: p.x + dx, y: p.y + dy, p: p.p })),
        extra: src.extra ? JSON.parse(JSON.stringify(src.extra)) : undefined,
      };
      s.bbox = strokeWorldBBox(s);
      boardStrokes.set(s.id, s);
      if (s.tool === "image" && s.extra && s.extra.mediaId) ensureMedia(s.extra.mediaId);
      wsSend({ type: "stroke_move", stroke: serializeStroke(s) });
      pushUndo({ type: "add", stroke: cloneStroke(s) });
      ids.add(s.id); boxes.push(s.bbox);
    }
    selection = { ids, bbox: unionBBox(boxes) };
    syncMediaToolbar();
    requestRedraw();
  }

  function bitmapToJpeg(source, maxEdge) {
    const w = source.width || source.naturalWidth, h = source.height || source.naturalHeight;
    const fit = Math.min(1, maxEdge / Math.max(w, h));
    const cw = Math.max(1, Math.round(w * fit)), ch = Math.max(1, Math.round(h * fit));
    const cnv = document.createElement("canvas");
    cnv.width = cw; cnv.height = ch;
    const c = cnv.getContext("2d");
    c.fillStyle = "#ffffff";
    c.fillRect(0, 0, cw, ch);
    c.drawImage(source, 0, 0, cw, ch);
    return { dataUrl: cnv.toDataURL("image/jpeg", 0.82), w: cw, h: ch };
  }
  async function uploadJpeg(dataUrl) {
    const id = uuid();
    const data = await API.post("/notes/media", { id, image: dataUrl });
    if (!data || !data.id) throw new Error("upload");
    return data.id;
  }
  function placeImageStroke(mediaId, w, h, name, origin) {
    const maxW = Math.min(720, window.innerWidth * 0.62) / scale;
    const maxH = Math.min(860, window.innerHeight * 0.7) / scale;
    const fit = Math.min(maxW / w, maxH / h, 1);
    const dw = w * fit, dh = h * fit, id = uuid();
    const stroke = {
      id, tool: "image", color: "#000000", size: 1,
      points: [{ x: origin.x, y: origin.y, p: 1 }, { x: origin.x + dw, y: origin.y + dh, p: 1 }],
      extra: { mediaId, crop: { l: 0, t: 0, r: 1, b: 1 }, nw: w, nh: h, name: name || "Bild" },
    };
    stroke.bbox = strokeWorldBBox(stroke);
    boardStrokes.set(id, stroke);
    ensureMedia(mediaId);
    wsSend({ type: "stroke_move", stroke: serializeStroke(stroke) });
    pushUndo({ type: "add", stroke: cloneStroke(stroke) });
    return stroke;
  }
  async function importImageFile(file, origin) {
    const bmp = await createImageBitmap(file);
    const jpeg = bitmapToJpeg(bmp, 1600);
    if (bmp.close) bmp.close();
    const mediaId = await uploadJpeg(jpeg.dataUrl);
    return placeImageStroke(mediaId, jpeg.w, jpeg.h, file.name, origin);
  }
  async function importPdfFile(file, origin) {
    if (!window.pdfjsLib) throw new Error("pdfjs");
    const buf = await file.arrayBuffer();
    const pdf = await window.pdfjsLib.getDocument({ data: buf }).promise;
    const n = Math.min(pdf.numPages, 6);
    const placed = [];
    let y = origin.y;
    for (let i = 1; i <= n; i++) {
      const page = await pdf.getPage(i);
      const base = page.getViewport({ scale: 1 });
      const scalePdf = Math.min(1.5, 1600 / Math.max(base.width, base.height));
      const vp = page.getViewport({ scale: scalePdf });
      const cnv = document.createElement("canvas");
      cnv.width = Math.max(1, Math.round(vp.width));
      cnv.height = Math.max(1, Math.round(vp.height));
      await page.render({ canvasContext: cnv.getContext("2d"), viewport: vp }).promise;
      const jpeg = bitmapToJpeg(cnv, 1600);
      const stroke = placeImageStroke(await uploadJpeg(jpeg.dataUrl), jpeg.w, jpeg.h, file.name + " S." + i, { x: origin.x, y });
      placed.push(stroke);
      y = stroke.points[1].y + 28;
    }
    return placed;
  }
  async function importFiles(fileList) {
    if (stopped) return;
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const worldOrigin = screenToWorld(window.innerWidth * 0.18, window.innerHeight * 0.16);
    let y = worldOrigin.y, x = worldOrigin.x;
    const ids = [];
    statusTextEl.textContent = "Importiere…";
    try {
      for (const file of files) {
        const isPdf = /pdf$/i.test(file.type) || /\.pdf$/i.test(file.name);
        if (isPdf) {
          const placed = await importPdfFile(file, { x, y });
          placed.forEach((s) => ids.push(s.id));
          if (placed.length) y = placed[placed.length - 1].points[1].y + 40;
        } else if (/^image\//.test(file.type) || /\.(png|jpe?g|gif|webp|heic)$/i.test(file.name)) {
          const s = await importImageFile(file, { x, y });
          ids.push(s.id);
          y = s.points[1].y + 40;
        }
      }
      if (ids.length) { selectStrokeIds(ids); }
    } catch (err) {
      console.warn("import failed", err);
      statusTextEl.textContent = "Import fehlgeschlagen";
      setTimeout(() => setConnected(!!(ws && ws.readyState === 1)), 1800);
      return;
    }
    setConnected(!!(ws && ws.readyState === 1));
    requestRedraw();
  }
  function onPointerDown(e) {
    try { canvas.setPointerCapture(e.pointerId); } catch (err) {}
    activePointers.set(e.pointerId, { type: e.pointerType, x: e.clientX, y: e.clientY });
    if (e.pointerType === "touch") {
      touchPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touchPointers.size === 2) {
        if (currentStroke) abortStroke();
        if (dragState) cancelSelectionDrag();
        pendingShapeDrag = null;
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
        const world = screenToWorld(e.clientX, e.clientY);
        if (currentTool !== "eraser" && pickShapedStrokeAt(world)) { dispatchPrimaryDown(e); return; }
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

  const importFileInput = document.getElementById("import-file");
  document.getElementById("btn-import")?.addEventListener("click", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (importFileInput) { importFileInput.value = ""; importFileInput.click(); }
  });
  importFileInput?.addEventListener("change", () => importFiles(importFileInput.files));
  document.getElementById("btn-media-crop")?.addEventListener("click", (e) => { e.stopPropagation(); enterCropMode(); });
  document.getElementById("btn-media-crop-done")?.addEventListener("click", (e) => { e.stopPropagation(); applyCropMode(); });
  document.getElementById("btn-media-crop-cancel")?.addEventListener("click", (e) => { e.stopPropagation(); cancelCropMode(); });
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && cropState) { e.preventDefault(); cancelCropMode(); } });
  window.addEventListener("dragover", (e) => {
    if (stopped) return;
    if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) e.preventDefault();
  });
  window.addEventListener("drop", (e) => {
    if (stopped) return;
    if (!e.dataTransfer || !e.dataTransfer.files || !e.dataTransfer.files.length) return;
    e.preventDefault();
    importFiles(e.dataTransfer.files);
  });
