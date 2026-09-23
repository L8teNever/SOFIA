
  function straightenOpenStroke(rawPoints) {
    if (!rawPoints || rawPoints.length < 4) return null;
    const start = rawPoints[0], end = rawPoints[rawPoints.length - 1];
    const chord = Math.hypot(end.x - start.x, end.y - start.y);
    if (chord < 28) return null;
    const bbox = makeBBox(rawPoints);
    const diagonal = Math.hypot(bbox.maxX - bbox.minX, bbox.maxY - bbox.minY);
    if (chord < diagonal * 0.38) return null;
    const avgPressure = rawPoints.reduce((s, p) => s + (p.p || 0.5), 0) / rawPoints.length;
    return { type: "line", points: [{ x: start.x, y: start.y, p: avgPressure }, { x: end.x, y: end.y, p: avgPressure }] };
  }
  function closedRing(pts) {
    if (!pts || pts.length < 2) return pts || [];
    const a = pts[0], b = pts[pts.length - 1];
    if (Math.hypot(a.x - b.x, a.y - b.y) < 1.5) return pts.slice(0, -1);
    return pts.slice();
  }
  function uniqueRectCorners(pts) {
    const ring = closedRing(pts);
    const p0 = (pts && pts[0] && pts[0].p) || 0.5;
    const four = ring.length >= 4 && ring.length <= 5 ? ring.slice(0, 4) : null;
    if (four && four.length === 4) return orderCornersCcw(four.map((p) => ({ x: p.x, y: p.y, p: p.p == null ? p0 : p.p })));
    const b = makeBBox(pts || []);
    return [{ x: b.minX, y: b.minY, p: p0 }, { x: b.maxX, y: b.minY, p: p0 }, { x: b.maxX, y: b.maxY, p: p0 }, { x: b.minX, y: b.maxY, p: p0 }];
  }
  function rebuildClosed(corners, pressure) {
    const p = pressure == null ? 0.5 : pressure;
    return corners.map((c) => ({ x: c.x, y: c.y, p })).concat([{ x: corners[0].x, y: corners[0].y, p }]);
  }
  function moveRectCorner(ordered, i, world, pressure) {
    const opp = ordered[(i + 2) % 4], prev = ordered[(i + 3) % 4], next = ordered[(i + 1) % 4];
    let ax = prev.x - opp.x, ay = prev.y - opp.y, bx = next.x - opp.x, by = next.y - opp.y;
    const al = Math.hypot(ax, ay) || 1, bl = Math.hypot(bx, by) || 1;
    ax /= al; ay /= al; bx /= bl; by /= bl;
    const dx = world.x - opp.x, dy = world.y - opp.y;
    const ua = Math.max(10, dx * ax + dy * ay), vb = Math.max(10, dx * bx + dy * by);
    const out = ordered.map((c) => ({ x: c.x, y: c.y, p: pressure }));
    out[(i + 2) % 4] = { x: opp.x, y: opp.y, p: pressure };
    out[(i + 3) % 4] = { x: opp.x + ax * ua, y: opp.y + ay * ua, p: pressure };
    out[(i + 1) % 4] = { x: opp.x + bx * vb, y: opp.y + by * vb, p: pressure };
    out[i] = { x: opp.x + ax * ua + bx * vb, y: opp.y + ay * ua + by * vb, p: pressure };
    return rebuildClosed(out, pressure);
  }
  function moveRectSide(ordered, i, world, pressure) {
    const a = ordered[i], b = ordered[(i + 1) % 4];
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    let ex = b.x - a.x, ey = b.y - a.y;
    const el = Math.hypot(ex, ey) || 1;
    ex /= el; ey /= el;
    let nx = -ey, ny = ex;
    const cx = ordered.reduce((s, p) => s + p.x, 0) / 4, cy = ordered.reduce((s, p) => s + p.y, 0) / 4;
    if ((mx - cx) * nx + (my - cy) * ny < 0) { nx = -nx; ny = -ny; }
    const dist = (world.x - mx) * nx + (world.y - my) * ny;
    const out = ordered.map((c) => ({ x: c.x, y: c.y, p: pressure }));
    out[i] = { x: a.x + nx * dist, y: a.y + ny * dist, p: pressure };
    out[(i + 1) % 4] = { x: b.x + nx * dist, y: b.y + ny * dist, p: pressure };
    const w = Math.hypot(out[i].x - out[(i + 3) % 4].x, out[i].y - out[(i + 3) % 4].y);
    const hh = Math.hypot(out[i].x - out[(i + 1) % 4].x, out[i].y - out[(i + 1) % 4].y);
    if (w < 10 || hh < 10) return rebuildClosed(ordered, pressure);
    return rebuildClosed(out, pressure);
  }
  function ellipseGeomFromPoints(pts) {
    const b = makeBBox(pts);
    return { cx: (b.minX + b.maxX) / 2, cy: (b.minY + b.maxY) / 2, rx: Math.max(8, (b.maxX - b.minX) / 2), ry: Math.max(8, (b.maxY - b.minY) / 2) };
  }
  function inferShape(stroke) {
    if (!stroke || stroke.tool === "image" || stroke.tool === "text") return null;
    if (stroke.extra && stroke.extra.shape) return stroke.extra.shape;
    const pts = stroke.points || [];
    if (pts.length === 2) return "line";
    const ring = closedRing(pts);
    if (pts.length >= 4 && pts.length <= 6) {
      const a = pts[0], b = pts[pts.length - 1];
      if (Math.hypot(a.x - b.x, a.y - b.y) < 14) {
        if (ring.length === 4) return "rectangle";
        if (ring.length === 3) return "triangle";
      }
    }
    if (looksLikePolygon(pts)) {
      if (ring.length === 4) return "rectangle";
      if (ring.length === 3) return "triangle";
    }
    if (pts.length >= 24) {
      const g = ellipseGeomFromPoints(pts);
      let sum = 0;
      for (const p of pts) sum += Math.abs(Math.hypot((p.x - g.cx) / (g.rx || 1), (p.y - g.cy) / (g.ry || 1)) - 1);
      if (sum / pts.length < 0.22) return Math.abs(g.rx - g.ry) / Math.max(g.rx, g.ry) < 0.18 ? "circle" : "ellipse";
    }
    return null;
  }
  function tagShape(stroke) {
    const shape = inferShape(stroke);
    if (!shape) return null;
    stroke.extra = Object.assign({}, stroke.extra || {}, { shape });
    return shape;
  }
  function pickLockedHandle(shape, pts, grab) {
    if (!grab) return { kind: "scale", i: 0 };
    if (shape === "rectangle") {
      const corners = uniqueRectCorners(pts);
      let best = { kind: "corner", i: 0, d: Infinity };
      for (let i = 0; i < corners.length; i++) {
        const d = Math.hypot(corners[i].x - grab.x, corners[i].y - grab.y);
        if (d < best.d) best = { kind: "corner", i, d };
      }
      for (let i = 0; i < corners.length; i++) {
        const a = corners[i], b = corners[(i + 1) % 4];
        const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        const d = Math.min(Math.hypot(mid.x - grab.x, mid.y - grab.y), distPointToSeg(grab, a, b));
        if (d < best.d + 24) best = { kind: "side", i, d };
      }
      return best;
    }
    if (shape === "line") {
      const a = pts[0], b = pts[pts.length - 1];
      return { kind: "end", i: Math.hypot(a.x - grab.x, a.y - grab.y) <= Math.hypot(b.x - grab.x, b.y - grab.y) ? 0 : pts.length - 1 };
    }
    return { kind: "scale", i: 0 };
  }
  function distPointToSeg(p, a, b) {
    const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
    if (l2 < 1e-8) return Math.hypot(p.x - a.x, p.y - a.y);
    let t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }
  function makeShapeGeom(shape, pts, grab) {
    const g = ellipseGeomFromPoints(pts);
    g.grabX = grab ? grab.x : g.cx + g.rx;
    g.grabY = grab ? grab.y : g.cy;
    g.grabR = Math.max(8, Math.hypot(g.grabX - g.cx, g.grabY - g.cy));
    g.base = (pts || []).map((p) => ({ x: p.x, y: p.y, p: p.p }));
    g.shape = shape;
    return g;
  }
  function isHoldSnapTool(tool) { return tool === "pen" || tool === "marker"; }
  function armHoldTimer() {
    clearHoldTimer();
    if (!shapeRecognitionEnabled) return;
    if (!currentStroke || !isHoldSnapTool(currentStroke.tool) || currentStroke.locked) return;
    holdTimer = setTimeout(tryShapeSnap, HOLD_MS);
  }
  function tryShapeSnap() {
    holdTimer = null;
    if (!currentStroke || !isHoldSnapTool(currentStroke.tool) || currentStroke.locked) return;
    let detected = detectShape(currentStroke.points);
    if (!detected && currentStroke.tool === "marker") detected = straightenOpenStroke(currentStroke.points);
    if (!detected) return;
    const grab = currentStroke.points[currentStroke.points.length - 1];
    currentStroke.points = detected.points;
    currentStroke.unsent = [];
    currentStroke.locked = true;
    const shapeName = detected.type === "circle" && detected.round === false ? "ellipse" : detected.type;
    currentStroke.extra = Object.assign({}, currentStroke.extra || {}, { shape: shapeName });
    currentStroke.shape = shapeName;
    currentStroke.shapeBase = detected.points.map((p) => ({ x: p.x, y: p.y, p: p.p }));
    currentStroke.shapeHandle = null;
    currentStroke.shapeHandleLocked = false;
    currentStroke.shapeGeom = makeShapeGeom(shapeName, detected.points, grab);
    wsSend({ type: "stroke_replace", strokeId: currentStroke.id, points: detected.points, extra: currentStroke.extra });
    requestRedraw();
  }
  function reshapeLockedStroke(wx, wy) {
    const s = currentStroke;
    if (!s || !s.locked || !s.shape) return;
    const p = (s.points[0] && s.points[0].p) || 0.5;
    const world = { x: wx, y: wy };
    if (s.shape === "rectangle") {
      const base = uniqueRectCorners(s.shapeBase || s.points);
      if (!s.shapeHandleLocked) {
        const gx = s.shapeGeom ? s.shapeGeom.grabX : world.x;
        const gy = s.shapeGeom ? s.shapeGeom.grabY : world.y;
        if (Math.hypot(wx - gx, wy - gy) < 8) return;
        s.shapeHandle = pickLockedHandle("rectangle", s.shapeBase || s.points, world);
        s.shapeHandleLocked = true;
      }
      const h = s.shapeHandle || { kind: "corner", i: 0 };
      s.points = h.kind === "side" ? moveRectSide(base, h.i, world, p) : moveRectCorner(base, h.i, world, p);
    } else if (s.shape === "circle" || s.shape === "ellipse") {
      const g = s.shapeGeom || ellipseGeomFromPoints(s.shapeBase || s.points);
      const d = Math.max(8, Math.hypot(wx - g.cx, wy - g.cy));
      if (s.shape === "circle") s.points = makeEllipsePoints(g.cx, g.cy, d, d, p, 96);
      else s.points = makeEllipsePoints(g.cx, g.cy, Math.max(8, g.rx * (d / (g.grabR || 1))), Math.max(8, g.ry * (d / (g.grabR || 1))), p, 96);
    } else if (s.shape === "line") {
      const pts = (s.shapeBase || s.points).map((pt) => ({ x: pt.x, y: pt.y, p: pt.p }));
      const i = (s.shapeHandle && s.shapeHandle.i) || pts.length - 1;
      pts[i] = { x: wx, y: wy, p };
      s.points = pts;
    } else if (s.shape === "triangle") {
      const g = s.shapeGeom;
      const d = Math.max(8, Math.hypot(wx - g.cx, wy - g.cy));
      const f = d / (g.grabR || 1);
      s.points = g.base.map((pt) => ({ x: g.cx + (pt.x - g.cx) * f, y: g.cy + (pt.y - g.cy) * f, p }));
    }
    s.bbox = makeBBox(s.points);
    wsSend({ type: "stroke_replace", strokeId: s.id, points: s.points, extra: s.extra });
    requestRedraw();
  }
  function startStroke(pointerId, pointerType, wx, wy, pressure) {
    clearSelection();
    const id = uuid();
    const tool = currentTool === "marker" ? "marker" : "pen";
    const size = activeSize();
    currentStroke = { id, tool, color: currentColor, size, points: [{ x: wx, y: wy, p: pressure }], unsent: [], pointerId, pointerType, locked: false };
    wsSend({ type: "stroke_start", strokeId: id, tool, color: currentColor, size, points: currentStroke.points });
    if (isHoldSnapTool(tool)) armHoldTimer();
    requestRedraw();
  }
  function extendStroke(wx, wy, pressure) {
    if (!currentStroke) return;
    if (currentStroke.locked) { reshapeLockedStroke(wx, wy); return; }
    const last = currentStroke.points[currentStroke.points.length - 1];
    const moved = !last || Math.hypot(wx - last.x, wy - last.y) >= MIN_MOVE_WORLD;
    if (!moved) return;
    const point = { x: wx, y: wy, p: pressure };
    currentStroke.points.push(point);
    currentStroke.unsent.push(point);
    if (isHoldSnapTool(currentStroke.tool)) armHoldTimer();
    requestRedraw();
  }
  function endStroke() {
    if (!currentStroke) return;
    clearHoldTimer();
    if (currentStroke.unsent.length > 0) {
      wsSend({ type: "stroke_points", strokeId: currentStroke.id, points: currentStroke.unsent });
      currentStroke.unsent = [];
    }
    if (currentStroke.extra && currentStroke.extra.shape) tagShape(currentStroke);
    wsSend({ type: "stroke_end", strokeId: currentStroke.id, extra: currentStroke.extra || null });
    currentStroke.bbox = strokeWorldBBox(currentStroke);
    boardStrokes.set(currentStroke.id, currentStroke);
    pushUndo({ type: "add", stroke: cloneStroke(currentStroke) });
    currentStroke = null;
    requestRedraw();
  }
  function strokeHitsPoint(stroke, pt, pad) {
    if (stroke.tool === "image") {
      const b = stroke.bbox || strokeWorldBBox(stroke);
      return b && pointInBBox(pt, b, pad);
    }
    const pts = stroke.points || [];
    const hitR = pad + stroke.size / 2;
    for (const p of pts) if (Math.hypot(p.x - pt.x, p.y - pt.y) <= hitR) return true;
    const shape = inferShape(stroke);
    if (shape === "rectangle" || shape === "triangle") {
      const poly = shape === "rectangle" ? uniqueRectCorners(pts) : closedRing(pts).slice(0, 3);
      if (poly.length >= 3 && pointInPolygon(pt, poly)) return true;
    }
    if (shape === "circle" || shape === "ellipse") {
      const g = ellipseGeomFromPoints(pts);
      const nx = (pt.x - g.cx) / (g.rx || 1), ny = (pt.y - g.cy) / (g.ry || 1);
      if (nx * nx + ny * ny <= 1) return true;
    }
    return false;
  }
  function eraseSegment(x0, y0, x1, y1) {
    const r = eraserSize / 2;
    const dist = Math.hypot(x1 - x0, y1 - y0);
    const steps = Math.max(1, Math.ceil(dist / Math.max(4, r * 0.5)));
    for (let i = 0; i <= steps; i++) {
      const t = i / steps;
      const sx = x0 + (x1 - x0) * t, sy = y0 + (y1 - y0) * t;
      const pt = { x: sx, y: sy };
      for (const stroke of boardStrokes.values()) {
        if (erasedThisGesture.has(stroke.id)) continue;
        const b = stroke.bbox || strokeWorldBBox(stroke);
        if (!b || sx < b.minX - r || sx > b.maxX + r || sy < b.minY - r || sy > b.maxY + r) continue;
        if (strokeHitsPoint(stroke, pt, r)) {
          erasedThisGesture.add(stroke.id);
          erasedStrokesThisGesture.set(stroke.id, cloneStroke(stroke));
        }
      }
    }
    if (erasedThisGesture.size > 0) {
      for (const id of erasedThisGesture) { boardStrokes.delete(id); pendingErase.add(id); }
      requestRedraw();
    }
  }
