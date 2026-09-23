
  function shapeEditKnots(stroke) {
    const shape = inferShape(stroke);
    const pts = stroke.points || [];
    if (shape === "rectangle") {
      const corners = uniqueRectCorners(pts);
      const knots = corners.map((c, i) => ({ kind: "corner", corner: i, i, x: c.x, y: c.y }));
      for (let i = 0; i < 4; i++) {
        const a = corners[i], b = corners[(i + 1) % 4];
        knots.push({ kind: "side", side: i, i: 100 + i, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
      }
      return knots;
    }
    if (shape === "circle" || shape === "ellipse") {
      const g = ellipseGeomFromPoints(pts);
      return [
        { kind: "radius", axis: "x", i: 0, x: g.cx + g.rx, y: g.cy },
        { kind: "radius", axis: "y", i: 1, x: g.cx, y: g.cy - g.ry },
        { kind: "radius", axis: "x", i: 2, x: g.cx - g.rx, y: g.cy },
        { kind: "radius", axis: "y", i: 3, x: g.cx, y: g.cy + g.ry },
      ];
    }
    if (shape === "triangle") return closedRing(pts).slice(0, 3).map((c, i) => ({ kind: "corner", corner: i, i, x: c.x, y: c.y }));
    if (shape === "line" && pts.length >= 2) {
      const last = pts.length - 1;
      return [{ kind: "end", i: 0, x: pts[0].x, y: pts[0].y }, { kind: "end", i: last, x: pts[last].x, y: pts[last].y }];
    }
    return null;
  }
  function pickStrokeAt(world) {
    const pad = 12 / Math.max(scale, 0.25);
    let best = null, bestD = Infinity;
    for (const s of boardStrokes.values()) {
      if (!strokeHitsPoint(s, world, pad)) continue;
      const b = s.bbox || strokeWorldBBox(s);
      const d = Math.hypot(world.x - (b.minX + b.maxX) / 2, world.y - (b.minY + b.maxY) / 2);
      if (d < bestD) { best = s; bestD = d; }
    }
    return best;
  }
  function pickShapedStrokeAt(world) {
    const hit = pickStrokeAt(world);
    if (hit && (inferShape(hit) || hit.tool === "image")) return hit;
    return null;
  }
  function selectedImageStroke() {
    if (selection.ids.size !== 1) return null;
    const s = boardStrokes.get(Array.from(selection.ids)[0]);
    return s && s.tool === "image" ? s : null;
  }
  function selectStrokeIds(ids) {
    const present = ids.filter((id) => boardStrokes.get(id));
    if (!present.length) { clearSelection(); return; }
    for (const id of present) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      const shape = tagShape(s);
      if (shape === "rectangle" && (s.points || []).length > 6) {
        const p = (s.points[0] && s.points[0].p) || 0.5;
        s.points = rebuildClosed(uniqueRectCorners(s.points), p);
        s.bbox = strokeWorldBBox(s);
      }
    }
    selection = {
      ids: new Set(present),
      bbox: unionBBox(present.map((id) => {
        const s = boardStrokes.get(id);
        return s && (s.bbox || strokeWorldBBox(s));
      }).filter(Boolean)),
    };
    syncMediaToolbar();
    requestRedraw();
  }
  function pickEditKnot(world) {
    if (selection.ids.size !== 1) return null;
    const s = boardStrokes.get(Array.from(selection.ids)[0]);
    if (!s) return null;
    const knots = shapeEditKnots(s);
    if (!knots) return null;
    const hit = 14 / Math.max(scale, 0.25);
    for (const k of knots) {
      if (Math.hypot(world.x - k.x, world.y - k.y) <= hit) return Object.assign({ strokeId: s.id, knots: knots.map((x) => x.i) }, k);
    }
    return null;
  }
  function clearSelection() {
    selection = { ids: new Set(), bbox: null };
    lassoPoints = null;
    lassoPointerId = null;
    dragState = null;
    pendingShapeDrag = null;
    cancelCropMode(true);
    syncMediaToolbar();
    requestRedraw();
  }
  function finalizeLasso() {
    const pts = lassoPoints;
    lassoPoints = null;
    const len = pts && pts.length > 1 ? pts.reduce((s, p, i) => i ? s + Math.hypot(p.x - pts[i - 1].x, p.y - pts[i - 1].y) : 0, 0) : 0;
    const tap = !pts || pts.length < 3 || len < 16 / Math.max(scale, 0.25);
    if (tap) {
      const hit = pts && pts[0] ? pickStrokeAt(pts[0]) : null;
      if (hit) selectStrokeIds([hit.id]);
      else clearSelection();
      return;
    }
    const ids = new Set();
    for (const stroke of boardStrokes.values()) {
      if (stroke.tool === "image") {
        const corners = imageRotatedCorners(stroke);
        if (corners.some((p) => pointInPolygon(p, pts))) ids.add(stroke.id);
        continue;
      }
      for (const p of stroke.points || []) {
        if (pointInPolygon(p, pts)) { ids.add(stroke.id); break; }
      }
    }
    if (ids.size === 0) { clearSelection(); return; }
    selectStrokeIds(Array.from(ids));
  }
  function snapshotSelection() {
    const snapshot = new Map(), extras = new Map(), sizes = new Map();
    for (const id of selection.ids) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      snapshot.set(id, s.points.map((p) => ({ x: p.x, y: p.y, p: p.p })));
      sizes.set(id, s.size);
      extras.set(id, s.extra ? JSON.parse(JSON.stringify(s.extra)) : null);
    }
    return { snapshot, extras, sizes };
  }
  function startSelectionDrag(pointerId, world) {
    dragState = { pointerId, startWorld: world, kind: "move", ...snapshotSelection() };
  }
  function startSelectionScale(pointerId, world, corner) {
    const pad = 10 / scale;
    const b = selection.bbox;
    const originMap = {
      nw: { x: b.maxX + pad, y: b.maxY + pad }, ne: { x: b.minX - pad, y: b.maxY + pad },
      sw: { x: b.maxX + pad, y: b.minY - pad }, se: { x: b.minX - pad, y: b.minY - pad },
      n: { x: (b.minX + b.maxX) / 2, y: b.maxY + pad }, s: { x: (b.minX + b.maxX) / 2, y: b.minY - pad },
      w: { x: b.maxX + pad, y: (b.minY + b.maxY) / 2 }, e: { x: b.minX - pad, y: (b.minY + b.maxY) / 2 },
    };
    dragState = { pointerId, startWorld: world, kind: "scale", corner, origin: originMap[corner] || originMap.se, ...snapshotSelection() };
  }
  function startSelectionRotate(pointerId, world) {
    const b = selection.bbox;
    dragState = { pointerId, startWorld: world, kind: "rotate", center: { x: (b.minX + b.maxX) / 2, y: (b.minY + b.maxY) / 2 }, ...snapshotSelection() };
  }
  function startPointEdit(pointerId, world, knot) {
    dragState = { pointerId, startWorld: world, kind: "point", strokeId: knot.strokeId, index: knot.i, knots: knot.knots, knot, ...snapshotSelection() };
  }
  function updateSelectionScale(world) {
    const origin = dragState.origin;
    const s0x = dragState.startWorld.x - origin.x, s0y = dragState.startWorld.y - origin.y;
    const s1x = world.x - origin.x, s1y = world.y - origin.y;
    let factor = Math.abs(s0x) > Math.abs(s0y) ? s1x / s0x : s1y / s0y;
    if (!Number.isFinite(factor)) factor = 1;
    factor = Math.max(0.08, Math.min(12, factor));
    const boxes = [];
    for (const [id, pts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      s.points = pts.map((p) => ({ x: origin.x + (p.x - origin.x) * factor, y: origin.y + (p.y - origin.y) * factor, p: p.p }));
      const baseSize = dragState.sizes && dragState.sizes.get(id);
      if (baseSize != null) s.size = Math.max(1, baseSize * factor);
      s.bbox = strokeWorldBBox(s);
      boxes.push(s.bbox);
    }
    selection.bbox = unionBBox(boxes);
    requestRedraw();
  }
  function updateSelectionDrag(world) {
    const dx = world.x - dragState.startWorld.x, dy = world.y - dragState.startWorld.y;
    const boxes = [];
    for (const [id, pts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      s.points = pts.map((p) => ({ x: p.x + dx, y: p.y + dy, p: p.p }));
      s.bbox = strokeWorldBBox(s);
      boxes.push(s.bbox);
    }
    selection.bbox = unionBBox(boxes);
    requestRedraw();
  }
  function updateSelectionRotate(world) {
    const c = dragState.center;
    const a0 = Math.atan2(dragState.startWorld.y - c.y, dragState.startWorld.x - c.x);
    let ang = Math.atan2(world.y - c.y, world.x - c.x) - a0;
    const step = Math.PI / 12, snapped = Math.round(ang / step) * step;
    if (Math.abs(ang - snapped) < (3.5 * Math.PI) / 180) ang = snapped;
    const boxes = [];
    for (const [id, pts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      if (s.tool === "image" && pts.length >= 2) {
        const minX = Math.min(pts[0].x, pts[1].x), minY = Math.min(pts[0].y, pts[1].y);
        const maxX = Math.max(pts[0].x, pts[1].x), maxY = Math.max(pts[0].y, pts[1].y);
        const ocx = (minX + maxX) / 2, ocy = (minY + maxY) / 2;
        const nc = rotatePoint({ x: ocx, y: ocy }, c.x, c.y, ang);
        s.points = pts.map((p) => ({ x: p.x + (nc.x - ocx), y: p.y + (nc.y - ocy), p: p.p }));
      } else {
        s.points = pts.map((p) => rotatePoint(p, c.x, c.y, ang));
      }
      const baseExtra = dragState.extras && dragState.extras.get(id);
      if (s.tool === "image") {
        const baseRot = baseExtra && Number.isFinite(baseExtra.rotation) ? baseExtra.rotation : 0;
        s.extra = Object.assign({}, baseExtra || s.extra || {}, { rotation: baseRot + ang });
      } else if (baseExtra) s.extra = JSON.parse(JSON.stringify(baseExtra));
      s.bbox = strokeWorldBBox(s);
      boxes.push(s.bbox);
    }
    selection.bbox = unionBBox(boxes);
    requestRedraw();
  }
  function updatePointEdit(world) {
    const id = dragState.strokeId;
    const pts = dragState.snapshot.get(id);
    const s = boardStrokes.get(id);
    if (!s || !pts) return;
    const knot = dragState.knot || { kind: "free", i: dragState.index };
    const pressure = (pts[0] && pts[0].p) || 0.5;
    const shape = inferShape({ ...s, points: pts, extra: s.extra });
    if (shape === "rectangle" && (knot.kind === "corner" || knot.kind === "side")) {
      const corners = uniqueRectCorners(pts);
      s.points = knot.kind === "side" ? moveRectSide(corners, knot.side, world, pressure) : moveRectCorner(corners, knot.corner, world, pressure);
      s.extra = Object.assign({}, s.extra || {}, { shape: "rectangle" });
    } else if ((shape === "circle" || shape === "ellipse") && knot.kind === "radius") {
      const g = ellipseGeomFromPoints(pts);
      if (shape === "circle") {
        const r = Math.max(8, Math.hypot(world.x - g.cx, world.y - g.cy));
        s.points = makeEllipsePoints(g.cx, g.cy, r, r, pressure, 96);
        s.extra = Object.assign({}, s.extra || {}, { shape: "circle" });
      } else {
        let rx = g.rx, ry = g.ry;
        if (knot.axis === "x") rx = Math.max(8, Math.abs(world.x - g.cx));
        else ry = Math.max(8, Math.abs(world.y - g.cy));
        s.points = makeEllipsePoints(g.cx, g.cy, rx, ry, pressure, 96);
        s.extra = Object.assign({}, s.extra || {}, { shape: "ellipse" });
      }
    } else if (shape === "triangle" && knot.kind === "corner") {
      const ring = closedRing(pts).slice(0, 3).map((p) => ({ x: p.x, y: p.y, p: pressure }));
      ring[knot.corner] = { x: world.x, y: world.y, p: pressure };
      s.points = rebuildClosed(ring, pressure);
      s.extra = Object.assign({}, s.extra || {}, { shape: "triangle" });
    } else if (shape === "line" && knot.kind === "end") {
      const next = pts.map((p) => ({ x: p.x, y: p.y, p: p.p }));
      next[knot.i] = { x: world.x, y: world.y, p: pressure };
      s.points = next;
      s.extra = Object.assign({}, s.extra || {}, { shape: "line" });
    }
    s.bbox = strokeWorldBBox(s);
    const boxes = [];
    for (const sid of selection.ids) {
      const st = boardStrokes.get(sid);
      if (st && st.bbox) boxes.push(st.bbox);
    }
    selection.bbox = unionBBox(boxes);
    requestRedraw();
  }
  function finalizeSelectionDrag() {
    if (!dragState) return;
    const moves = [];
    for (const [id, beforePts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      wsSend({ type: "stroke_move", stroke: serializeStroke(s) });
      const extra = dragState.extras && dragState.extras.get(id);
      moves.push({
        id, before: beforePts, after: s.points.map((p) => ({ ...p })),
        beforeExtra: extra || null, afterExtra: s.extra ? JSON.parse(JSON.stringify(s.extra)) : null,
        beforeSize: dragState.sizes && dragState.sizes.get(id), afterSize: s.size,
      });
    }
    if (moves.length) pushUndo({ type: "move", moves });
    dragState = null;
    pendingShapeDrag = null;
  }
  function cancelSelectionDrag() {
    if (!dragState) return;
    for (const [id, pts] of dragState.snapshot) {
      const s = boardStrokes.get(id);
      if (!s) continue;
      s.points = pts.map((p) => ({ ...p }));
      if (dragState.extras && dragState.extras.has(id)) {
        const ex = dragState.extras.get(id);
        if (ex) s.extra = JSON.parse(JSON.stringify(ex));
      }
      s.bbox = strokeWorldBBox(s);
    }
    dragState = null;
    requestRedraw();
  }
  function beginStrokeInteraction(pointerId, world, stroke) {
    selectStrokeIds([stroke.id]);
    const pad = 10 / scale;
    const knot = pickEditKnot(world);
    if (knot) { startPointEdit(pointerId, world, knot); return; }
    if (selection.bbox) {
      const handle = pickScaleHandle(world, selection.bbox, pad);
      if (handle) { startSelectionScale(pointerId, world, handle); return; }
      if (pickRotateHandle(world, selection.bbox, pad)) { startSelectionRotate(pointerId, world); return; }
    }
    pendingShapeDrag = { pointerId, startWorld: world, strokeId: stroke.id };
  }
  function cropHandlePoints(full, crop) {
    const x0 = full.minX + crop.l * full.w, y0 = full.minY + crop.t * full.h;
    const x1 = full.minX + crop.r * full.w, y1 = full.minY + crop.b * full.h;
    const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
    return [
      { name: "nw", x: x0, y: y0 }, { name: "n", x: mx, y: y0 }, { name: "ne", x: x1, y: y0 },
      { name: "w", x: x0, y: my }, { name: "e", x: x1, y: my },
      { name: "sw", x: x0, y: y1 }, { name: "s", x: mx, y: y1 }, { name: "se", x: x1, y: y1 },
    ];
  }
  function pickCropHandle(world) {
    if (!cropState || !cropState.full) return null;
    const r = 16 / Math.max(scale, 0.25);
    for (const p of cropHandlePoints(cropState.full, cropState.crop)) {
      if (Math.hypot(world.x - p.x, world.y - p.y) <= r) return p.name;
    }
    return null;
  }
  function clampCrop(c) {
    const min = 0.04;
    let l = Math.max(0, Math.min(1 - min, c.l)), t = Math.max(0, Math.min(1 - min, c.t));
    let r = Math.max(l + min, Math.min(1, c.r)), b = Math.max(t + min, Math.min(1, c.b));
    return { l, t, r, b };
  }
  function updateCropDrag(world) {
    if (!cropState || !cropState.handle || !cropState.startCrop) return;
    const full = cropState.full, start = cropState.startCrop;
    const dx = (world.x - cropState.startWorld.x) / full.w, dy = (world.y - cropState.startWorld.y) / full.h;
    const next = { ...start }, h = cropState.handle;
    if (h === "move") {
      const w = start.r - start.l, ht = start.b - start.t;
      next.l = start.l + dx; next.t = start.t + dy; next.r = next.l + w; next.b = next.t + ht;
      if (next.l < 0) { next.r -= next.l; next.l = 0; }
      if (next.t < 0) { next.b -= next.t; next.t = 0; }
      if (next.r > 1) { next.l -= next.r - 1; next.r = 1; }
      if (next.b > 1) { next.t -= next.b - 1; next.b = 1; }
    } else {
      if (h.indexOf("w") >= 0) next.l = start.l + dx;
      if (h.indexOf("e") >= 0) next.r = start.r + dx;
      if (h.indexOf("n") >= 0) next.t = start.t + dy;
      if (h.indexOf("s") >= 0) next.b = start.b + dy;
    }
    cropState.crop = clampCrop(next);
    requestRedraw();
  }
  function enterCropMode() {
    const s = selectedImageStroke();
    if (!s) return;
    const full = imageFullRect(s);
    if (!full) return;
    cropState = { strokeId: s.id, full, crop: imageCrop(s), before: cloneStroke(s), pointerId: null, handle: null };
    syncMediaToolbar();
    requestRedraw();
  }
  function cancelCropMode(silent) {
    if (!cropState) return;
    cropState = null;
    if (!silent) syncMediaToolbar();
    requestRedraw();
  }
  function applyCropMode() {
    if (!cropState) return;
    const s = boardStrokes.get(cropState.strokeId);
    if (!s) { cropState = null; return; }
    const full = cropState.full, crop = clampCrop(cropState.crop);
    const minX = full.minX + crop.l * full.w, minY = full.minY + crop.t * full.h;
    const maxX = full.minX + crop.r * full.w, maxY = full.minY + crop.b * full.h;
    const before = cropState.before;
    s.points = [{ x: minX, y: minY, p: 1 }, { x: maxX, y: maxY, p: 1 }];
    s.extra = Object.assign({}, s.extra || {}, { crop });
    s.bbox = strokeWorldBBox(s);
    wsSend({ type: "stroke_move", stroke: serializeStroke(s) });
    pushUndo({ type: "move", moves: [{ id: s.id, before: before.points.map((p) => ({ ...p })), after: s.points.map((p) => ({ ...p })), beforeExtra: before.extra || null, afterExtra: JSON.parse(JSON.stringify(s.extra)) }] });
    cropState = null;
    selectStrokeIds([s.id]);
  }
  function syncMediaToolbar() {
    const bar = document.getElementById("selection-toolbar");
    const cropBtn = document.getElementById("btn-media-crop");
    const doneBtn = document.getElementById("btn-media-crop-done");
    const cancelBtn = document.getElementById("btn-media-crop-cancel");
    const copyBtn = document.getElementById("btn-sel-copy");
    const cutBtn = document.getElementById("btn-sel-cut");
    const pasteBtn = document.getElementById("btn-sel-paste");
    const cropping = !!cropState;
    const img = selectedImageStroke();
    if (copyBtn) copyBtn.classList.toggle("hidden", cropping || !selection.ids.size);
    if (cutBtn) cutBtn.classList.toggle("hidden", cropping || !selection.ids.size);
    if (pasteBtn) pasteBtn.classList.toggle("hidden", cropping || !strokeClipboard.length);
    if (cropBtn) cropBtn.classList.toggle("hidden", cropping || !img);
    if (doneBtn) doneBtn.classList.toggle("hidden", !cropping);
    if (cancelBtn) cancelBtn.classList.toggle("hidden", !cropping);
    if (!bar) return;
    if (!cropping && !selection.ids.size) { bar.classList.add("hidden"); return; }
    bar.classList.remove("hidden");
  }
  function positionSelectionToolbar() {
    const bar = document.getElementById("selection-toolbar");
    if (!bar) return;
    syncMediaToolbar();
    const s = selectedImageStroke() || (cropState && boardStrokes.get(cropState.strokeId));
    const b = cropState && cropState.full ? cropState.full : (selection.bbox || (s && strokeWorldBBox(s)));
    if (!b) { if (!cropState) bar.classList.add("hidden"); return; }
    const scr = worldToScreen((b.minX + b.maxX) / 2, b.minY);
    bar.style.left = scr.x + "px";
    bar.style.top = Math.max(72, scr.y - 12) + "px";
  }
