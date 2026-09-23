  let cropState = null;
  let pendingShapeDrag = null;
  const mediaImages = new Map();

  function serializeStroke(s) {
    const out = { id: s.id, tool: s.tool, color: s.color, size: s.size, points: s.points };
    if (s.extra) out.extra = s.extra;
    return out;
  }
  function cloneStroke(s) {
    const out = { id: s.id, tool: s.tool, color: s.color, size: s.size, points: (s.points || []).map((p) => ({ ...p })) };
    if (s.extra) out.extra = JSON.parse(JSON.stringify(s.extra));
    return out;
  }
  function hydrateStroke(s) {
    if (!s) return s;
    if (s.points && !Array.isArray(s.points) && s.points.pts) {
      if (!s.extra && s.points.extra) s.extra = s.points.extra;
      s.points = s.points.pts;
    }
    if (!Array.isArray(s.points)) s.points = [];
    if (s.tool === "image" && s.extra && s.extra.mediaId) ensureMedia(s.extra.mediaId);
    s.bbox = strokeWorldBBox(s);
    return s;
  }
  function wsStroke(s) { return serializeStroke(s); }

  function looksLikePolygon(pts) {
    if (!pts || pts.length === 2) return !!pts && pts.length === 2;
    if (pts.length < 3 || pts.length > 6) return false;
    const a = pts[0], b = pts[pts.length - 1];
    return Math.hypot(a.x - b.x, a.y - b.y) < 6;
  }
  function rotatePoint(p, cx, cy, ang) {
    const c = Math.cos(ang), s = Math.sin(ang), dx = p.x - cx, dy = p.y - cy;
    return { x: cx + dx * c - dy * s, y: cy + dx * s + dy * c, p: p.p, text: p.text };
  }
  function strokeRotation(stroke) {
    const r = stroke && stroke.extra && stroke.extra.rotation;
    return Number.isFinite(r) ? r : 0;
  }
  function imageDestRect(stroke) {
    const pts = stroke.points || [];
    if (pts.length < 2) return null;
    const minX = Math.min(pts[0].x, pts[1].x), minY = Math.min(pts[0].y, pts[1].y);
    const maxX = Math.max(pts[0].x, pts[1].x), maxY = Math.max(pts[0].y, pts[1].y);
    return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY };
  }
  function imageCrop(stroke) {
    const c = (stroke.extra && stroke.extra.crop) || {};
    const l = Math.max(0, Math.min(0.98, c.l == null ? 0 : c.l));
    const t = Math.max(0, Math.min(0.98, c.t == null ? 0 : c.t));
    const r = Math.max(l + 0.02, Math.min(1, c.r == null ? 1 : c.r));
    const b = Math.max(t + 0.02, Math.min(1, c.b == null ? 1 : c.b));
    return { l, t, r, b };
  }
  function imageFullRect(stroke) {
    const dest = imageDestRect(stroke);
    if (!dest) return null;
    const crop = imageCrop(stroke);
    const fw = dest.w / (crop.r - crop.l), fh = dest.h / (crop.b - crop.t);
    const minX = dest.minX - crop.l * fw, minY = dest.minY - crop.t * fh;
    return { minX, minY, maxX: minX + fw, maxY: minY + fh, w: fw, h: fh };
  }
  function imageRotatedCorners(stroke) {
    const dest = imageDestRect(stroke);
    if (!dest) return [];
    const cx = dest.minX + dest.w / 2, cy = dest.minY + dest.h / 2, rot = strokeRotation(stroke);
    const pts = [
      { x: dest.minX, y: dest.minY }, { x: dest.maxX, y: dest.minY },
      { x: dest.maxX, y: dest.maxY }, { x: dest.minX, y: dest.maxY },
    ];
    return rot ? pts.map((p) => rotatePoint(p, cx, cy, rot)) : pts;
  }
  function strokeWorldBBox(stroke) {
    if (!stroke) return null;
    if (stroke.tool === "image") {
      const corners = imageRotatedCorners(stroke);
      if (corners.length) return makeBBox(corners);
    }
    return makeBBox(stroke.points || []);
  }
  function mediaUrl(mediaId) {
    return "/uploads/notes-media/" + encodeURIComponent(mediaId) + ".jpg";
  }
  function ensureMedia(mediaId) {
    if (!mediaId) return null;
    let img = mediaImages.get(mediaId);
    if (img) return img;
    img = new Image();
    img.decoding = "async";
    img.onload = () => requestRedraw();
    img.src = mediaUrl(mediaId);
    mediaImages.set(mediaId, img);
    return img;
  }
  function drawImageStroke(c, stroke) {
    const dest = imageDestRect(stroke);
    if (!dest || dest.w < 1 || dest.h < 1) return;
    const extra = stroke.extra || {};
    const showingCrop = cropState && cropState.strokeId === stroke.id;
    const rect = showingCrop ? cropState.full : dest;
    const crop = showingCrop ? cropState.crop : imageCrop(stroke);
    const img = ensureMedia(extra.mediaId);
    c.save();
    if (!img || !img.complete || !img.naturalWidth) {
      c.fillStyle = "#e8eaed";
      c.fillRect(dest.minX, dest.minY, dest.w, dest.h);
      c.strokeStyle = "#9aa0a6";
      c.lineWidth = 1.5 / scale;
      c.strokeRect(dest.minX, dest.minY, dest.w, dest.h);
      c.restore();
      return;
    }
    const nw = img.naturalWidth, nh = img.naturalHeight;
    const sx = crop.l * nw, sy = crop.t * nh;
    const sw = Math.max(1, (crop.r - crop.l) * nw), sh = Math.max(1, (crop.b - crop.t) * nh);
    if (showingCrop) {
      c.globalAlpha = 0.38;
      c.drawImage(img, rect.minX, rect.minY, rect.w, rect.h);
      c.globalAlpha = 1;
      const cx = rect.minX + crop.l * rect.w, cy = rect.minY + crop.t * rect.h;
      c.drawImage(img, sx, sy, sw, sh, cx, cy, (crop.r - crop.l) * rect.w, (crop.b - crop.t) * rect.h);
    } else {
      const rot = strokeRotation(stroke);
      c.translate(dest.minX + dest.w / 2, dest.minY + dest.h / 2);
      if (rot) c.rotate(rot);
      c.drawImage(img, sx, sy, sw, sh, -dest.w / 2, -dest.h / 2, dest.w, dest.h);
    }
    c.restore();
  }
  function drawStroke(stroke) {
    const pts = stroke.points || [];
    if (stroke.tool === "image") { drawImageStroke(ctx, stroke); return; }
    if (!pts.length) return;
    const isMarker = stroke.tool === "marker";
    ctx.globalAlpha = isMarker ? 0.35 : 1;
    ctx.strokeStyle = stroke.color;
    ctx.fillStyle = stroke.color;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const tagged = stroke.extra && stroke.extra.shape;
    const constant = tagged === "rectangle" || tagged === "triangle" || tagged === "line" || tagged === "circle" || tagged === "ellipse" || looksLikePolygon(pts);
    if (pts.length === 1) {
      ctx.beginPath();
      ctx.arc(pts[0].x, pts[0].y, (constant ? stroke.size : widthAt(stroke.size, pts[0].p)) / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
      return;
    }
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.lineWidth = constant ? stroke.size : widthAt(stroke.size, (pts[0].p + pts[pts.length - 1].p) / 2);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  function draw() {
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#f8f9fa";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(scale * dpr, 0, 0, scale * dpr, offsetX * dpr, offsetY * dpr);
    drawGrid();
    function each(pred, fn) {
      for (const s of boardStrokes.values()) if (pred(s)) fn(s);
      for (const s of remoteInProgress.values()) if (pred(s)) fn(s);
      if (currentStroke && currentStroke.tool && pred(currentStroke)) fn(currentStroke);
    }
    each((s) => s.tool === "image", drawStroke);
    each((s) => s.tool === "marker", drawStroke);
    each((s) => s.tool && s.tool !== "marker" && s.tool !== "image", drawStroke);
    drawLassoAndSelection();
    if (zoomIndicatorEl) zoomIndicatorEl.textContent = Math.round(scale * 100) + "%";
    repositionPresenceLabels();
  }

  function putStroke(stroke) {
    const withBBox = hydrateStroke({ ...stroke, points: (stroke.points || []).map((p) => ({ ...p })), extra: stroke.extra ? JSON.parse(JSON.stringify(stroke.extra)) : stroke.extra });
    boardStrokes.set(withBBox.id, withBBox);
    wsSend({ type: "stroke_move", stroke: serializeStroke(withBBox) });
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
        const extra = direction === 1 ? m.afterExtra : m.beforeExtra;
        if (s) {
          s.points = points.map((p) => ({ ...p }));
          if (extra) s.extra = JSON.parse(JSON.stringify(extra));
          else if (m.afterExtra || m.beforeExtra) s.extra = extra || undefined;
          if (m.beforeSize != null) s.size = direction === 1 ? (m.afterSize != null ? m.afterSize : s.size) : m.beforeSize;
          s.bbox = strokeWorldBBox(s);
          wsSend({ type: "stroke_move", stroke: serializeStroke(s) });
        }
      }
    }
    requestRedraw();
  }

  function finalizeIncomingStroke(id) {
    const s = remoteInProgress.get(id);
    if (!s) return;
    remoteInProgress.delete(id);
    if (s.points.length > 0) {
      hydrateStroke(s);
      boardStrokes.set(s.id, s);
    }
  }
  function handleMessage(msg) {
    switch (msg.type) {
      case "init": {
        myClientId = msg.clientId;
        boardStrokes.clear();
        for (const s of msg.strokes) { hydrateStroke(s); boardStrokes.set(s.id, s); }
        requestRedraw();
        break;
      }
      case "presence_join": ensurePresence(msg.id, msg.color); break;
      case "presence_leave": removePresence(msg.id); break;
      case "cursor": {
        const p = ensurePresence(msg.id, msg.color);
        p.x = msg.x; p.y = msg.y; p.tool = msg.tool; p.size = msg.size; p.lastSeen = performance.now();
        requestRedraw();
        break;
      }
      case "stroke_start":
        remoteInProgress.set(msg.strokeId, hydrateStroke({ id: msg.strokeId, tool: msg.tool, color: msg.color, size: msg.size, points: msg.points || [], extra: msg.extra, ownerId: msg.id }));
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
        if (s) {
          s.points = msg.points;
          if (msg.extra) s.extra = msg.extra;
        }
        requestRedraw();
        break;
      }
      case "stroke_end": finalizeIncomingStroke(msg.strokeId); requestRedraw(); break;
      case "stroke_abort": remoteInProgress.delete(msg.strokeId); requestRedraw(); break;
      case "stroke_move": {
        const s = msg.stroke;
        if (s && s.id) { hydrateStroke(s); boardStrokes.set(s.id, s); requestRedraw(); }
        break;
      }
      case "erase":
        for (const id of msg.strokeIds) { boardStrokes.delete(id); remoteInProgress.delete(id); }
        requestRedraw();
        break;
    }
  }

  function resampleByLength(points, spacing) {
    if (points.length < 2) return points.slice();
    const out = [{ x: points[0].x, y: points[0].y, p: points[0].p }];
    let acc = 0;
    for (let i = 1; i < points.length; i++) {
      let x0 = points[i - 1].x, y0 = points[i - 1].y;
      const x1 = points[i].x, y1 = points[i].y;
      let seg = Math.hypot(x1 - x0, y1 - y0);
      while (acc + seg >= spacing) {
        const t = (spacing - acc) / seg;
        x0 += (x1 - x0) * t; y0 += (y1 - y0) * t;
        out.push({ x: x0, y: y0, p: points[i].p });
        seg = Math.hypot(x1 - x0, y1 - y0);
        acc = 0;
      }
      acc += seg;
    }
    const last = points[points.length - 1];
    const tail = out[out.length - 1];
    if (Math.hypot(last.x - tail.x, last.y - tail.y) > 0.5) out.push({ x: last.x, y: last.y, p: last.p });
    return out;
  }
  function turnAbs(a, b, c) {
    const v1x = b.x - a.x, v1y = b.y - a.y, v2x = c.x - b.x, v2y = c.y - b.y;
    const l1 = Math.hypot(v1x, v1y), l2 = Math.hypot(v2x, v2y);
    if (l1 < 1e-6 || l2 < 1e-6) return 0;
    return Math.abs(Math.atan2(v1x * v2y - v1y * v2x, v1x * v2x + v1y * v2y));
  }
  function findDominantCorners(rawPoints, diagonal, closed) {
    const spacing = Math.max(3, diagonal * 0.018);
    const pts = resampleByLength(rawPoints, spacing);
    const n = pts.length;
    if (n < 6) return rdpSimplify(rawPoints, diagonal * 0.05);
    const k = Math.max(2, Math.round((diagonal * 0.045) / spacing));
    const scores = new Array(n).fill(0);
    for (let i = 0; i < n; i++) {
      if (!closed && (i < k || i >= n - k)) continue;
      scores[i] = turnAbs(pts[(i - k + n) % n], pts[i], pts[(i + k) % n]);
    }
    const minAngle = (40 * Math.PI) / 180;
    const minSep = Math.max(4, Math.round((diagonal * 0.12) / spacing));
    const peaks = [];
    for (let i = 0; i < n; i++) {
      if (scores[i] < minAngle) continue;
      let isMax = true;
      for (let d = 1; d <= minSep; d++) {
        if (scores[(i + d) % n] > scores[i] || scores[(i - d + n) % n] > scores[i]) { isMax = false; break; }
      }
      if (isMax) peaks.push({ i, score: scores[i], p: pts[i] });
    }
    peaks.sort((a, b) => b.score - a.score);
    const chosen = [];
    for (const peak of peaks) {
      const tooClose = chosen.some((c) => Math.min(Math.abs(c.i - peak.i), n - Math.abs(c.i - peak.i)) < minSep);
      if (!tooClose) chosen.push(peak);
    }
    chosen.sort((a, b) => a.i - b.i);
    if (chosen.length >= 3 && chosen.length <= 6) return chosen.map((c) => ({ x: c.p.x, y: c.p.y }));
    const maxScore = scores.reduce((m, s) => (s > m ? s : m), 0);
    if (closed && maxScore < minAngle) return [];
    const simplified = rdpSimplify(rawPoints, diagonal * 0.07);
    if (closed && simplified.length >= 4) return simplified.slice(0, simplified.length - 1);
    return simplified;
  }
  function collapseToQuad(corners, diagonal) {
    if (!corners || corners.length === 4) return corners || null;
    if (corners.length !== 5 && corners.length !== 6) return null;
    let pts = corners.map((p) => ({ x: p.x, y: p.y }));
    while (pts.length > 4) {
      let drop = 0, best = Infinity;
      for (let i = 0; i < pts.length; i++) {
        const a = pts[(i - 1 + pts.length) % pts.length], b = pts[i], c = pts[(i + 1) % pts.length];
        const score = turnAbs(a, b, c) * (Math.hypot(b.x - a.x, b.y - a.y) + Math.hypot(c.x - b.x, c.y - b.y));
        if (score < best) { best = score; drop = i; }
      }
      pts.splice(drop, 1);
    }
    const minSide = Math.min(...pts.map((p, i) => Math.hypot(pts[(i + 1) % 4].x - p.x, pts[(i + 1) % 4].y - p.y)));
    return minSide < diagonal * 0.12 ? null : pts;
  }
  function orderCornersCcw(corners) {
    const cx = corners.reduce((s, p) => s + p.x, 0) / corners.length;
    const cy = corners.reduce((s, p) => s + p.y, 0) / corners.length;
    return corners.slice().sort((a, b) => Math.atan2(a.y - cy, a.x - cx) - Math.atan2(b.y - cy, b.x - cx));
  }
  function fitOrientedRect(corners, avgPressure) {
    const pts = orderCornersCcw(corners);
    let bestLen = 0, ux = 1, uy = 0;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i], b = pts[(i + 1) % pts.length];
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy);
      if (len > bestLen) { bestLen = len; ux = dx / len; uy = dy / len; }
    }
    const angle = Math.atan2(uy, ux);
    const snapped = Math.round(angle / (Math.PI / 2)) * (Math.PI / 2);
    if (Math.abs(angle - snapped) < (15 * Math.PI) / 180) { ux = Math.cos(snapped); uy = Math.sin(snapped); }
    const vx = -uy, vy = ux;
    let minU = Infinity, maxU = -Infinity, minV = Infinity, maxV = -Infinity;
    for (const p of pts) {
      const u = p.x * ux + p.y * uy, v = p.x * vx + p.y * vy;
      if (u < minU) minU = u; if (u > maxU) maxU = u; if (v < minV) minV = v; if (v > maxV) maxV = v;
    }
    let du = maxU - minU, dv = maxV - minV;
    if (du > 0 && Math.abs(du - dv) / Math.max(du, dv) < 0.22) {
      const side = (du + dv) / 2, cu = (minU + maxU) / 2, cv = (minV + maxV) / 2;
      minU = cu - side / 2; maxU = cu + side / 2; minV = cv - side / 2; maxV = cv + side / 2;
    }
    const cornerAt = (u, v) => ({ x: u * ux + v * vx, y: u * uy + v * vy, p: avgPressure });
    const quad = [cornerAt(minU, minV), cornerAt(maxU, minV), cornerAt(maxU, maxV), cornerAt(minU, maxV)];
    return [...quad, quad[0]];
  }
  function bboxEdgeFraction(points, bbox) {
    const w = bbox.maxX - bbox.minX, h = bbox.maxY - bbox.minY;
    const tol = Math.max(4, Math.min(w, h) * 0.055);
    let near = 0;
    for (const p of points) {
      if (Math.min(Math.abs(p.x - bbox.minX), Math.abs(p.x - bbox.maxX), Math.abs(p.y - bbox.minY), Math.abs(p.y - bbox.maxY)) <= tol) near++;
    }
    return near / points.length;
  }
  function makeEllipsePoints(cx, cy, rx, ry, avgPressure, n) {
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const t = (i / n) * Math.PI * 2;
      pts.push({ x: cx + rx * Math.cos(t), y: cy + ry * Math.sin(t), p: avgPressure });
    }
    return pts;
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
    const closed = startEndDist < diagonal * 0.38;
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
    centroid.x /= rawPoints.length; centroid.y /= rawPoints.length;
    const radii = rawPoints.map((p) => Math.hypot(p.x - centroid.x, p.y - centroid.y));
    const meanR = radii.reduce((a, b) => a + b, 0) / radii.length;
    const variance = radii.reduce((a, r) => a + (r - meanR) * (r - meanR), 0) / radii.length;
    const circleFit = meanR > 0 ? Math.sqrt(variance) / meanR : 1;
    const periFit = meanR > 0 ? Math.abs(pathLength - 2 * Math.PI * meanR) / (2 * Math.PI * meanR) : 1;
    const aspectDiff = Math.max(w, h) > 0 ? Math.abs(w - h) / Math.max(w, h) : 1;
    const boxy = bboxEdgeFraction(rawPoints, bbox);
    const rectPeri = 2 * (w + h);
    const rectFit = rectPeri > 0 ? Math.abs(pathLength - rectPeri) / rectPeri : 1;
    const circular = circleFit < 0.26 && periFit < 0.30 && rectFit > 0.10 && boxy < 0.72;
    const corners = findDominantCorners(rawPoints, diagonal, true);
    const quad = collapseToQuad(corners, diagonal);
    const hasSharpQuad = !!(quad && quad.length === 4 && corners.length >= 3 && corners.length <= 6);
    if (hasSharpQuad) return { type: "rectangle", points: fitOrientedRect(quad, avgPressure) };
    if (boxy >= 0.72 && rectFit < 0.16 && !circular) {
      const aabb = [{ x: bbox.minX, y: bbox.minY, p: avgPressure }, { x: bbox.maxX, y: bbox.minY, p: avgPressure }, { x: bbox.maxX, y: bbox.maxY, p: avgPressure }, { x: bbox.minX, y: bbox.maxY, p: avgPressure }];
      return { type: "rectangle", points: [...aabb, aabb[0]] };
    }
    if (corners.length === 3 && !(circular && circleFit < 0.18)) {
      return { type: "triangle", points: [...corners, corners[0]].map((p) => ({ x: p.x, y: p.y, p: avgPressure })) };
    }
    if (circular || (circleFit < 0.20 && periFit < 0.26 && rectFit > 0.08)) {
      const useCircle = aspectDiff < 0.18;
      const rx = useCircle ? meanR : w / 2, ry = useCircle ? meanR : h / 2;
      return { type: "circle", round: useCircle, points: makeEllipsePoints(cx, cy, rx, ry, avgPressure, 96) };
    }
    if (quad && quad.length === 4) return { type: "rectangle", points: fitOrientedRect(quad, avgPressure) };
    return null;
  }
