// Fehlerprotokoll: bemerkt die App (PWA/Browser) einen Fehler, geht er automatisch mit
// Zeitstempel, Seite, Version, Geraet und den letzten Bedienschritten an den Server
// (POST /api/v1/client-log -> Logdatei auf dem Server). Ohne Netz wird gesammelt und
// spaeter nachgeschickt. Wird als erstes Skript geladen, damit auch Startfehler ankommen.
(function () {
  const QKEY = 'sofia-errlog-q';
  const crumbs = [];
  const sent = new Map(); // Meldung -> zuletzt gesendet
  const hhmmss = () => new Date().toTimeString().slice(0, 8);

  function action(text) {
    crumbs.push(hhmmss() + ' ' + String(text).slice(0, 140));
    if (crumbs.length > 40) crumbs.shift();
  }

  async function send(entry) {
    try {
      const r = await fetch('/api/v1/client-log', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry), keepalive: true, credentials: 'same-origin',
      });
      if (!r.ok) throw new Error('log ' + r.status);
      return true;
    } catch (e) {
      try {
        const q = JSON.parse(localStorage.getItem(QKEY) || '[]');
        q.push(entry);
        localStorage.setItem(QKEY, JSON.stringify(q.slice(-20)));
      } catch (_) {}
      return false;
    }
  }

  async function flush() {
    let q = [];
    try { q = JSON.parse(localStorage.getItem(QKEY) || '[]'); localStorage.removeItem(QKEY); } catch (_) {}
    for (const e of q) await send(e);
  }

  function report(kind, message, stack, extra) {
    try {
      message = String(message || '').slice(0, 800);
      if (!message) return;
      const key = kind + '|' + message;
      const now = Date.now();
      if (sent.has(key) && now - sent.get(key) < 60000) return; // gleiche Meldung nicht dauernd
      sent.set(key, now);
      if (sent.size > 50) sent.delete(sent.keys().next().value);
      send({
        clientTime: new Date().toString().slice(0, 33),
        kind,
        message,
        stack: stack ? String(stack) : '',
        where: Object.assign({
          url: location.pathname + location.search,
          page: (typeof currentPage !== 'undefined' && currentPage) || null,
          screen: innerWidth + 'x' + innerHeight,
        }, extra || {}),
        version: window.__APP_VERSION__ || '',
        standalone: !!(window.matchMedia && matchMedia('(display-mode: standalone)').matches) || !!navigator.standalone,
        online: navigator.onLine,
        ua: navigator.userAgent,
        actions: crumbs.slice(),
      });
    } catch (_) {}
  }

  window.addEventListener('error', (e) => {
    const t = e.target;
    if (t && t !== window && t.tagName) {
      // Skript/Stylesheet nicht ladbar ist ein echter Fehler, fehlende Bilder nicht
      if (t.tagName === 'SCRIPT' || t.tagName === 'LINK') report('load', 'Konnte nicht laden: ' + (t.src || t.href));
      return;
    }
    report('error', e.message, e.error && e.error.stack, { file: String(e.filename || '').split('/').pop() + ':' + e.lineno + ':' + e.colno });
  }, true);
  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason;
    if (r && r.name === 'AbortError') return;
    report('promise', (r && r.message) || String(r), r && r.stack, r && r.status ? { status: r.status } : null);
  });

  // Bedienschritte: Antippen von Knoepfen/Kacheln (Beschriftung/ID, keine Eingaben)
  document.addEventListener('click', (e) => {
    const b = e.target && e.target.closest && e.target.closest('button, a, [onclick], [data-page], label, .widget');
    if (!b) return;
    const name = b.id || b.getAttribute('data-page') || b.title || b.getAttribute('aria-label') ||
      (b.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40) || b.className;
    action('tap ' + String(name).slice(0, 70));
  }, true);
  window.addEventListener('popstate', () => action('zurück → ' + location.pathname));
  window.addEventListener('online', flush);
  setTimeout(() => { if (navigator.onLine) flush(); }, 4000);

  window.sofiaLog = { action, report };
})();
