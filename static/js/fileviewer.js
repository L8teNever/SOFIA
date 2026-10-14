// Datei-Ansicht fuer die ganze App: Dateien (PDFs, Anhaenge, Moodle, Chat ...) oeffnen
// im Vollbild mit lila Leiste oben (X = zurueck, Herunterladen) statt ohne Rueckweg im
// App-Fenster. PDFs zeigt pdf.js (mitgeliefert), Bilder direkt, sonst Herunterladen.
// Greift automatisch bei Links auf eigene Dateien (target=_blank oder download);
// Links auf fremde Seiten bleiben wie sie sind.
(function () {
  const PDFJS = '/static/vendor/pdfjs/'; // pdf.js 3.11.174
  let pdfLib = null;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function loadPdfJs() {
    if (pdfLib) return pdfLib;
    pdfLib = new Promise((res, rej) => {
      if (window.pdfjsLib) return res(window.pdfjsLib);
      const sc = document.createElement('script');
      sc.src = PDFJS + 'pdf.min.js';
      sc.onload = () => { window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS + 'pdf.worker.min.js'; res(window.pdfjsLib); };
      sc.onerror = () => { pdfLib = null; rej(new Error('pdf.js nicht ladbar')); };
      document.head.appendChild(sc);
    });
    return pdfLib;
  }

  // Name + Unterzeile: Moodle-Proxy-Links tragen die Originaladresse in ?u=
  function info(href, name) {
    let host = location.host, fromUrl = '';
    try {
      const u = new URL(href, location.href);
      const src = u.searchParams.get('u');
      if (src) { const o = new URL(src); host = o.host; fromUrl = o.pathname; }
      else fromUrl = u.pathname;
    } catch (e) {}
    let last = '';
    try { last = decodeURIComponent(fromUrl.split('/').pop() || ''); } catch (e) { last = fromUrl.split('/').pop() || ''; }
    name = (name || '').trim();
    if (!name || name === 'file') name = last || 'Datei';
    return { name, host, ext: ((/\.([a-z0-9]{1,5})$/i.exec(name) || /\.([a-z0-9]{1,5})$/i.exec(last) || [])[1] || '').toLowerCase() };
  }

  function close(v, fromPop) {
    if (!v || v.classList.contains('closing')) return;
    v.classList.remove('active');
    v.classList.add('closing');
    setTimeout(() => v.remove(), 250);
    // Nur den eigenen Verlaufseintrag still zuruecknehmen - sonst verliesse "zurueck" die App
    if (!fromPop && history.state && history.state.fileViewer) {
      if (typeof isSelfPopping !== 'undefined') isSelfPopping = true;
      history.back();
    }
  }

  function open(href, name) {
    const fi = info(href, name);
    document.querySelectorAll('.file-viewer').forEach((x) => x.remove());
    const v = document.createElement('div');
    v.className = 'file-viewer';
    v.innerHTML = `
      <div class="fv-bar">
        <button type="button" class="fv-close" title="Schließen"><i data-lucide="x" style="width:24px;height:24px;"></i></button>
        <div class="fv-title"><b></b><small></small></div>
        <a class="fv-dl" title="Herunterladen"><i data-lucide="download" style="width:22px;height:22px;"></i></a>
      </div>
      <div class="fv-body"><div class="fv-msg">Wird geladen…</div></div>`;
    v.querySelector('.fv-title b').textContent = fi.name;
    v.querySelector('.fv-title small').textContent = fi.host;
    const dl = v.querySelector('.fv-dl');
    dl.href = href;
    dl.setAttribute('download', fi.name);
    v.querySelector('.fv-close').onclick = (e) => { e.stopPropagation(); close(v); };
    document.body.appendChild(v);
    if (window.lucide) lucide.createIcons();
    requestAnimationFrame(() => requestAnimationFrame(() => v.classList.add('active')));
    history.pushState(Object.assign({}, history.state || {}, { fileViewer: true }), '', location.pathname);
    render(href, fi, v.querySelector('.fv-body'), v);
  }

  async function render(href, fi, body, v) {
    const failMsg = (t) => { body.innerHTML = `<div class="fv-msg">${t}<br><a href="${esc(href)}" download="${esc(fi.name)}">Herunterladen</a></div>`; };
    let blob;
    try {
      const r = await fetch(href, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(r.status);
      blob = await r.blob();
    } catch (e) {
      if (v.isConnected) failMsg('Die Datei konnte nicht geladen werden.');
      return;
    }
    if (!v.isConnected) return;
    const type = (blob.type || '').toLowerCase();
    const ext = fi.ext;
    if (type.includes('pdf') || ext === 'pdf') return renderPdf(await blob.arrayBuffer(), body, v, failMsg);
    if (type.startsWith('image/') || /^(png|jpe?g|gif|webp|bmp)$/.test(ext)) {
      const url = URL.createObjectURL(blob);
      body.innerHTML = `<img class="fv-img" src="${url}" alt="">`;
      return;
    }
    if (type.startsWith('text/plain') || ext === 'txt') {
      const pre = document.createElement('pre');
      pre.style.cssText = 'margin:0;padding:16px;white-space:pre-wrap;word-break:break-word;font-size:0.9rem;background:#fff;min-height:100%;box-sizing:border-box;';
      pre.textContent = await blob.text();
      body.innerHTML = '';
      body.appendChild(pre);
      return;
    }
    failMsg('Für diese Datei gibt es keine Vorschau.');
  }

  async function renderPdf(data, body, v, failMsg) {
    try {
      const lib = await loadPdfJs();
      const pdf = await lib.getDocument({ data }).promise;
      if (!v.isConnected) return;
      const wrap = document.createElement('div');
      wrap.className = 'fv-pages';
      body.innerHTML = '';
      body.appendChild(wrap);
      const slots = [];
      for (let i = 1; i <= Math.min(pdf.numPages, 150); i++) {
        const page = await pdf.getPage(i);
        const vp = page.getViewport({ scale: 1 });
        const d = document.createElement('div');
        d.className = 'fv-page';
        d.style.aspectRatio = vp.width + ' / ' + vp.height;
        wrap.appendChild(d);
        slots.push({ page, d, done: 0 });
      }
      const draw = async (s) => {
        const want = Math.min(2600, Math.round(s.d.clientWidth * (window.devicePixelRatio || 1) * 1.3));
        if (s.done >= want || s.busy) return;
        s.busy = true;
        const base = s.page.getViewport({ scale: 1 });
        const vp = s.page.getViewport({ scale: want / base.width });
        const c = document.createElement('canvas');
        c.width = Math.round(vp.width); c.height = Math.round(vp.height);
        try { await s.page.render({ canvasContext: c.getContext('2d'), viewport: vp }).promise; } catch (e) { s.busy = false; return; }
        s.d.innerHTML = ''; s.d.appendChild(c); s.done = want; s.busy = false;
      };
      const io = new IntersectionObserver((es) => es.forEach((e) => {
        if (e.isIntersecting) draw(slots.find((s) => s.d === e.target));
      }), { root: body, rootMargin: '600px 0px' });
      slots.forEach((s) => io.observe(s.d));
      // Doppeltipp: heranzoomen / zurueck
      let last = 0;
      wrap.addEventListener('click', (e) => {
        const now = Date.now();
        if (now - last < 320) {
          const top = body.getBoundingClientRect().top;
          const rx = (e.clientX + body.scrollLeft) / wrap.offsetWidth, ry = (e.clientY - top + body.scrollTop) / wrap.offsetHeight;
          wrap.classList.toggle('zoom');
          body.scrollLeft = rx * wrap.offsetWidth - body.clientWidth / 2;
          body.scrollTop = ry * wrap.offsetHeight - body.clientHeight / 2;
          slots.forEach((s) => { const r = s.d.getBoundingClientRect(); if (r.bottom > -600 && r.top < innerHeight + 600) draw(s); });
          last = 0;
        } else last = now;
      });
    } catch (e) {
      if (v.isConnected) failMsg('Die PDF konnte nicht angezeigt werden.');
    }
  }

  // Eigene Dateien automatisch in der Ansicht oeffnen
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || a.closest('.file-viewer') || a.hasAttribute('data-no-viewer')) return;
    if (a.target !== '_blank' && !a.hasAttribute('download')) return;
    const raw = a.getAttribute('href');
    if (!raw || raw === '#' || raw.startsWith('javascript:') || raw.startsWith('blob:') || raw.startsWith('data:')) return;
    let u;
    try { u = new URL(raw, location.href); } catch (err) { return; }
    if (u.origin !== location.origin) return; // fremde Seiten wie gehabt
    e.preventDefault();
    e.stopPropagation();
    const name = a.getAttribute('data-name') || a.getAttribute('download') || '';
    open(u.pathname + u.search, name);
  }, true);

  window.openFileViewer = open;
  // fuer den Zurueck-Knopf/die Geste (app.js popstate): oberste Ansicht schliessen
  window.closeTopFileViewer = function () {
    const vs = [...document.querySelectorAll('.file-viewer:not(.closing)')];
    if (!vs.length) return false;
    close(vs[vs.length - 1], true);
    return true;
  };
})();
