let currentUser = null;
let currentPage = null;
let pageHistory = [];

function showToast(msg, duration = 3000) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.opacity = '1';
  t.style.transform = 'translateX(-50%) translateY(0)';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => {
    t.style.opacity = '0';
    t.style.transform = 'translateX(-50%) translateY(20px)';
  }, duration);
}

function runScripts(container) {
  container.querySelectorAll('script').forEach(function(old) {
    var s = document.createElement('script');
    if (old.src) {
      for (var i = 0; i < old.attributes.length; i++) {
        var a = old.attributes[i];
        s.setAttribute(a.name, a.value);
      }
    } else {
      // Rewrite top-level let/const to var: re-opening a page re-executes
      // its <script>, and let/const in script-scope throw "already declared"
      // on the second run. var allows redeclaration. Page-level onclick
      // handlers and init_<page>() must stay on the global scope, so we
      // can't wrap in an IIFE.
      s.textContent = old.textContent.replace(/^(\s*)(?:let|const)\s+/gm, '$1var ');
    }
    old.parentNode.replaceChild(s, old);
  });
}

async function openPage(name, triggerEl) {
  if (currentPage === name) return;
  let ox = '50%', oy = '50%';
  if (triggerEl) {
    const r = triggerEl.getBoundingClientRect();
    ox = (r.left + r.width / 2) + 'px';
    oy = (r.top + r.height / 2) + 'px';
  }
  let html;
  try {
    const res = await fetch('/pages/' + name + '.html', { cache: 'no-store' });
    if (!res.ok) throw new Error('not found');
    html = await res.text();
  } catch {
    showToast('Seite nicht gefunden');
    return;
  }
  const existing = document.getElementById('page-container').querySelector('.page');
  if (existing) existing.remove();
  const container = document.getElementById('page-container');
  container.innerHTML = html;
  runScripts(container);
  const page = container.querySelector('.page') || container.firstElementChild;
  if (!page) return;
  page.style.setProperty('--ox', ox);
  page.style.setProperty('--oy', oy);
  document.body.appendChild(page);
  container.innerHTML = '';
  requestAnimationFrame(() => requestAnimationFrame(() => page.classList.add('active')));
  history.pushState({ page: name }, '', '/' + name);
  pageHistory.push(name);
  currentPage = name;
  if (window.lucide) lucide.createIcons();
  enhanceDropdowns(page);
  const initFn = window['init_' + name];
  if (initFn) initFn();
}

function closePage() {
  const page = document.querySelector('.page.active');
  if (!page) return;
  const wasNotifications = currentPage === 'notifications';
  page.classList.remove('active');
  page.classList.add('closing');
  setTimeout(() => page.remove(), 600);
  pageHistory.pop();
  currentPage = pageHistory[pageHistory.length - 1] || null;
  const prev = pageHistory[pageHistory.length - 1];
  history.pushState({ page: prev }, '', prev ? '/' + prev : '/');
  if (wasNotifications) refreshNotifBadge();
}

document.addEventListener('click', e => { if (e.target.closest('.back-btn')) closePage(); });
window.addEventListener('popstate', () => {
  const page = document.querySelector('.page.active');
  if (page) { page.classList.remove('active'); page.classList.add('closing'); setTimeout(() => page.remove(), 600); currentPage = null; }
});

function enhanceDropdowns(root) {
  (root || document).querySelectorAll('select.m3-select:not([data-dd])').forEach(sel => {
    sel.setAttribute('data-dd', '1');

    const wrap = document.createElement('div');
    wrap.className = 'm3-dropdown';
    ['flex','flexGrow','flexShrink','width','maxWidth','minWidth','margin','marginBottom','marginTop'].forEach(p => {
      if (sel.style[p]) wrap.style[p] = sel.style[p];
    });
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);

    const chevSvg = '<svg class="m3-dropdown-chev" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'm3-dropdown-btn';
    btn.innerHTML = '<span class="m3-dropdown-lbl"></span>' + chevSvg;
    wrap.appendChild(btn);

    // Panel lives outside the sheet (portaled to body) to escape overflow + transform clipping
    const panel = document.createElement('div');
    panel.className = 'm3-dropdown-panel';

    let offHandler = null;

    function buildOpts() {
      panel.innerHTML = '';
      Array.from(sel.options).forEach(opt => {
        const div = document.createElement('div');
        div.className = 'm3-dropdown-opt' + (opt.selected ? ' dd-sel' : '');
        div.dataset.value = opt.value;
        div.textContent = opt.text;
        div.addEventListener('mousedown', e => e.preventDefault());
        div.addEventListener('click', () => {
          sel.value = opt.value;
          sel.dispatchEvent(new Event('change', {bubbles: true}));
          close();
          syncLabel();
          syncSel();
        });
        panel.appendChild(div);
      });
    }

    function syncLabel() {
      const i = sel.selectedIndex;
      btn.querySelector('.m3-dropdown-lbl').textContent = i >= 0 && sel.options[i] ? sel.options[i].text : '';
    }
    function syncSel() {
      panel.querySelectorAll('.m3-dropdown-opt').forEach((d, i) => {
        d.classList.toggle('dd-sel', !!(sel.options[i] && sel.options[i].selected));
      });
    }

    function posPanel() {
      const r = btn.getBoundingClientRect();
      panel.style.left  = r.left + 'px';
      panel.style.width = r.width + 'px';
      panel.style.right = 'auto';
      const below = window.innerHeight - r.bottom - 8;
      if (below >= 120 || below >= r.top) {
        panel.style.top    = (r.bottom + 5) + 'px';
        panel.style.bottom = 'auto';
        panel.style.transformOrigin = 'top';
      } else {
        panel.style.bottom = (window.innerHeight - r.top + 5) + 'px';
        panel.style.top    = 'auto';
        panel.style.transformOrigin = 'bottom';
        panel.style.transform = panel.classList.contains('dd-open') ? 'none' : 'scaleY(0.9) translateY(6px)';
      }
    }

    function open() {
      document.querySelectorAll('.m3-dropdown-panel.dd-open').forEach(p => {
        p.classList.remove('dd-open');
        if (p.parentNode) p.parentNode.removeChild(p);
      });
      document.querySelectorAll('.m3-dropdown.dd-open').forEach(d => d.classList.remove('dd-open'));

      document.body.appendChild(panel);
      posPanel();
      wrap.classList.add('dd-open');
      requestAnimationFrame(() => panel.classList.add('dd-open'));

      offHandler = e => { if (!wrap.contains(e.target) && !panel.contains(e.target)) close(); };
      setTimeout(() => document.addEventListener('click', offHandler), 0);
    }

    function close() {
      wrap.classList.remove('dd-open');
      panel.classList.remove('dd-open');
      setTimeout(() => { if (panel.parentNode) panel.parentNode.removeChild(panel); }, 150);
      if (offHandler) { document.removeEventListener('click', offHandler); offHandler = null; }
    }

    btn.addEventListener('click', e => { e.stopPropagation(); wrap.classList.contains('dd-open') ? close() : open(); });
    sel.addEventListener('change', () => { syncLabel(); syncSel(); });
    new MutationObserver(() => { buildOpts(); syncLabel(); }).observe(sel, {childList: true});

    buildOpts();
    syncLabel();
  });
}

function openSheet(html) {
  let sheet = document.querySelector('.bottom-sheet');
  if (!sheet) { sheet = document.createElement('div'); sheet.className = 'bottom-sheet'; document.body.appendChild(sheet); }
  sheet.innerHTML = '<div class="sheet-handle"></div>' + html;
  enhanceDropdowns(sheet);
  if (window.lucide) lucide.createIcons();
  document.getElementById('bottom-sheet-scrim').classList.add('active');
  requestAnimationFrame(() => sheet.classList.add('active'));
}
function closeSheet() {
  const sheet = document.querySelector('.bottom-sheet');
  if (sheet) { sheet.classList.remove('active'); setTimeout(() => sheet.remove(), 400); }
  document.getElementById('bottom-sheet-scrim').classList.remove('active');
}
document.getElementById('bottom-sheet-scrim').addEventListener('click', closeSheet);

function openModal(html) {
  document.getElementById('modal-content').innerHTML = html;
  document.getElementById('modal-scrim').classList.add('active');
  enhanceDropdowns(document.getElementById('modal-content'));
  if (window.lucide) lucide.createIcons();
}
function closeModal() { document.getElementById('modal-scrim').classList.remove('active'); }
document.getElementById('modal-scrim').addEventListener('click', e => {
  if (e.target === document.getElementById('modal-scrim')) closeModal();
});

async function loadDashboard() {
  try {
    const [hw, events, grades, files] = await Promise.all([
      API.homework().catch(() => []),
      API.events().catch(() => []),
      API.grades().catch(() => []),
      API.files().catch(() => []),
    ]);
    const today = new Date().toISOString().slice(0, 10);
    const openHw = hw.filter(h => h.due_date >= today && !(h.checked_by || []).includes(currentUser && currentUser.id));
    document.getElementById('w-hw-count').textContent = openHw.length;
    const upcoming = events.filter(e => e.date >= today).sort((a, b) => a.date.localeCompare(b.date));
    if (upcoming.length) {
      const ev = upcoming[0];
      const d = new Date(ev.date + 'T00:00');
      const isTomorrow = ev.date === new Date(Date.now()+86400000).toISOString().slice(0,10);
      const dayLabel = ev.date === today ? 'Heute' : isTomorrow ? 'Morgen' : d.toLocaleDateString('de-DE', { weekday: 'short', day: 'numeric', month: 'short' });
      document.getElementById('w-next-event').textContent = ev.title;
      const sub = document.getElementById('w-next-event-sub');
      if (sub) sub.textContent = dayLabel + (ev.time ? ' · ' + ev.time : '');
    }
    if (grades.length) {
      const avg = (grades.reduce((s, g) => s + g.value, 0) / grades.length).toFixed(1);
      document.getElementById('w-grade-avg').textContent = avg;
    }
    document.getElementById('w-files-count').textContent = files.length;
  } catch (e) {}
}

function updateGreeting() {
  const now = new Date();
  const h = now.getHours();
  const greeting = h < 12 ? 'Guten Morgen' : h < 18 ? 'Guten Tag' : 'Guten Abend';
  const name = (currentUser && (currentUser.display_name || currentUser.email.split('@')[0])) || '';
  document.getElementById('greeting-name').textContent = greeting + (name ? ', ' + name : '') + '!';
  document.getElementById('greeting-date').textContent = now.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' });
}

function showNotActivated(email) {
  const overlay = document.getElementById('intro-overlay');
  overlay.style.transition = 'none';
  overlay.style.opacity = '1';
  overlay.style.pointerEvents = 'all';
  overlay.innerHTML =
    '<div class="m3-blob" style="width:400px;height:400px;background:#d0bcff;top:-100px;left:-100px;opacity:0.3;"></div>' +
    '<div style="position:relative;z-index:1;text-align:center;padding:40px;max-width:360px;">' +
      '<div style="width:72px;height:72px;background:#ffd8e4;border-radius:24px;display:flex;align-items:center;justify-content:center;margin:0 auto 24px auto;">' +
        '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#31111d" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' +
      '</div>' +
      '<div style="font-size:1.5rem;font-weight:700;color:#e6e1e5;margin-bottom:8px;">Kein Zugang</div>' +
      '<div style="color:#cac4d0;line-height:1.6;font-size:0.95rem;">' +
        'Das Konto' + (email ? ' <strong>' + email + '</strong>' : '') + ' wurde noch nicht freigeschaltet.<br><br>' +
        'Bitte wende dich an den Administrator.' +
      '</div>' +
      '<button onclick="showLoginScreen()" style="margin-top:20px;padding:12px 24px;background:#2b2930;color:#e6e1e5;border:none;border-radius:14px;cursor:pointer;font-size:0.9rem;">Andere E-Mail verwenden</button>' +
    '</div>';
}

function runIntro() {
  const name = currentUser && (currentUser.display_name || currentUser.email.split('@')[0]);
  const lines = name ? ['Willkommen zurück,', name] : ['Willkommen zurück'];
  const container = document.getElementById('intro-text');
  container.style.flexDirection = 'column';
  container.style.alignItems = 'center';
  container.style.gap = '4px';
  let charIdx = 0;
  lines.forEach(function(line, li) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;justify-content:center;gap:0;' + (li === 1 ? 'font-size:2.8rem;font-weight:800;' : 'font-size:1.1rem;font-weight:500;opacity:0.7;');
    line.split('').forEach(function(ch) {
      const span = document.createElement('span');
      span.className = 'flying-char';
      span.textContent = ch === ' ' ? ' ' : ch;
      span.style.transitionDelay = (charIdx * 45) + 'ms';
      if (li === 1) span.style.fontSize = 'inherit';
      row.appendChild(span);
      charIdx++;
    });
    container.appendChild(row);
  });
  requestAnimationFrame(function() {
    document.querySelectorAll('.flying-char').forEach(function(c) {
      c.style.opacity = '1';
      c.style.transform = 'scale(1) translateY(0)';
    });
  });
  setTimeout(function() {
    const overlay = document.getElementById('intro-overlay');
    overlay.style.opacity = '0';
    overlay.style.pointerEvents = 'none';
    setTimeout(function() { if (overlay.parentNode) overlay.remove(); }, 900);
    showApp();
  }, charIdx * 45 + 700);
}

const KNOWN_PAGES = ['calendar','homework','grades','timetable','chat','drive','quickshare','settings','admin','notifications'];

function showApp() {
  document.getElementById('app').classList.add('ready');
  document.querySelectorAll('.widget').forEach(function(w, i) {
    setTimeout(function() { w.classList.add('pop'); }, i * 70);
  });
  if (window.lucide) lucide.createIcons();
  loadDashboard();
  // Auto-open page if URL path matches a known page (e.g. navigating to /homework directly)
  const urlPage = window.location.pathname.replace(/^\//, '').split('/')[0];
  if (urlPage && KNOWN_PAGES.includes(urlPage)) {
    setTimeout(function() { openPage(urlPage); }, 400);
  }
}

async function boot() {
  try {
    currentUser = await API.me();
  } catch (err) {
    let email = '';
    try { const c = await fetch('/api/v1/auth/check'); const j = await c.json(); email = j.email || ''; } catch {}
    showNotActivated(email);
    return;
  }
  const didOnboard = await checkAndRunOnboarding();
  if (currentUser.role === 'admin' || currentUser.role === 'super_admin') {
    document.getElementById('admin-widget').style.display = 'flex';
  }
  updateGreeting();
  const sn = document.getElementById('w-settings-name');
  if (sn) sn.textContent = (currentUser.display_name || currentUser.email.split('@')[0]) + ' · ' + (currentUser.class_name || '');
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function() {});
  }
  await Push.init();
  refreshNotifBadge();
  runIntro();
}

async function refreshNotifBadge() {
  try {
    const { count } = await API.unreadCount();
    const badge = document.getElementById('notif-badge');
    if (badge) badge.style.display = count > 0 ? 'block' : 'none';
  } catch {}
}

boot();