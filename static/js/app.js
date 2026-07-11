let currentUser = null;
let currentPage = null;
let pageHistory = [];
let isSelfPopping = false;

// Keep --app-height in sync with the real visible viewport (visualViewport,
// where available) instead of relying on vh/dvh alone. Mobile Safari doesn't
// shrink vh when the keyboard opens, and dvh support/behavior is still
// inconsistent across iOS versions — this covers both plus browser
// chrome show/hide, so body always matches what's actually on screen.
function updateAppHeight() {
  const h = (window.visualViewport ? window.visualViewport.height : window.innerHeight);
  document.documentElement.style.setProperty('--app-height', h + 'px');
}
updateAppHeight();
window.addEventListener('resize', updateAppHeight);
window.addEventListener('orientationchange', updateAppHeight);
if (window.visualViewport) {
  window.visualViewport.addEventListener('resize', updateAppHeight);
}

// Returns an inline-style fragment that paints a user's avatar_url as the
// element's background (used on the existing .avatar circles instead of
// swapping in <img> tags, so all the initials-based markup keeps working).
function avatarImgStyle(user) {
  return (user && user.avatar_url) ? `background-image:url('${user.avatar_url}');background-size:cover;background-position:center;` : '';
}
function applyAvatarEl(el, user, fallbackText) {
  if (!el) return;
  if (user && user.avatar_url) {
    el.style.backgroundImage = `url('${user.avatar_url}')`;
    el.style.backgroundSize = 'cover';
    el.style.backgroundPosition = 'center';
    el.textContent = '';
  } else {
    el.style.backgroundImage = '';
    el.textContent = fallbackText || '';
  }
}

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

async function openPage(name, triggerEl, preserveUrl = false) {
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
  
  if (!preserveUrl) {
    history.pushState({ page: name }, '', '/' + name);
  } else {
    history.replaceState({ page: name }, '', window.location.pathname);
  }
  pageHistory.push(name);
  currentPage = name;
  if (window.lucide) lucide.createIcons();
  enhanceDropdowns(page);
  const initFn = window['init_' + name];
  if (initFn) initFn();
}

function closePage() {
  closeSheet(true);
  closeModal(true);
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
window.addEventListener('popstate', (e) => {
  if (isSelfPopping) {
    isSelfPopping = false;
    return;
  }
  const modalActive = document.getElementById('modal-scrim')?.classList.contains('active');
  const sheetActive = document.querySelector('.bottom-sheet.active');
  
  if (modalActive) {
    closeModal(true);
    return;
  }
  if (sheetActive) {
    closeSheet(true);
    return;
  }

  // If a chat room is open, close it instead of the whole page
  if (typeof closeChatRoom === 'function' && document.getElementById('page-chat-room')) {
    closeChatRoom(true);
    return;
  }
  
  const page = document.querySelector('.page.active');
  if (page) { 
    page.classList.remove('active'); 
    page.classList.add('closing'); 
    setTimeout(() => page.remove(), 600); 
    currentPage = null; 
  }
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
  
  history.pushState({ page: currentPage, sheet: true }, '', window.location.pathname);
}
function closeSheet(isPopState = false) {
  const sheet = document.querySelector('.bottom-sheet');
  if (sheet) { sheet.classList.remove('active'); setTimeout(() => sheet.remove(), 400); }
  document.getElementById('bottom-sheet-scrim').classList.remove('active');
  
  if (!isPopState && history.state && history.state.sheet) {
    isSelfPopping = true;
    history.back();
  }
}
document.getElementById('bottom-sheet-scrim').addEventListener('click', () => closeSheet(false));
 
function openModal(html) {
  document.getElementById('modal-content').innerHTML = html;
  document.getElementById('modal-scrim').classList.add('active');
  enhanceDropdowns(document.getElementById('modal-content'));
  if (window.lucide) lucide.createIcons();
  
  history.pushState({ page: currentPage, modal: true }, '', window.location.pathname);
}
function closeModal(isPopState = false) { 
  document.getElementById('modal-scrim').classList.remove('active'); 
  
  if (!isPopState && history.state && history.state.modal) {
    isSelfPopping = true;
    history.back();
  }
}
document.getElementById('modal-scrim').addEventListener('click', e => {
  if (e.target === document.getElementById('modal-scrim')) closeModal(false);
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
    
    // Fetch and update Timetable widget
    API.timetable().then(ttData => {
      if (ttData && ttData.configured && !ttData.error) {
        const lessons = (ttData.this_week?.lessons || []).concat(ttData.next_week?.lessons || []);
        const now = new Date();
        const y = now.getFullYear();
        const m = String(now.getMonth() + 1).padStart(2, '0');
        const d = String(now.getDate()).padStart(2, '0');
        const todayStr = `${y}${m}${d}`;
        const currentHHMM = now.getHours() * 100 + now.getMinutes();
        
        // Find lessons for today
        const todayLessons = lessons.filter(l => l.date === todayStr && !l.cancelled);
        const widgetLessonEl = document.getElementById('w-current-lesson');
        if (!widgetLessonEl) return;
        const subLabel = widgetLessonEl.previousElementSibling;
        
        if (todayLessons.length) {
          // Find current active lesson
          const active = todayLessons.find(l => currentHHMM >= l.startTime && currentHHMM <= l.endTime);
          
          if (active) {
            const subject = active.subject_short || active.subject || '–';
            const room = active.room ? ` (${active.room})` : '';
            widgetLessonEl.textContent = `${subject}${room}`;
            if (subLabel) subLabel.textContent = 'Jetzt';
          } else {
            // Find next upcoming lesson today
            const upcoming = todayLessons
              .filter(l => l.startTime > currentHHMM)
              .sort((a, b) => a.startTime - b.startTime);
              
            if (upcoming.length) {
              const next = upcoming[0];
              const subject = next.subject_short || next.subject || '–';
              const startStr = String(next.startTime).padStart(4, '0');
              const fmtStart = `${startStr.slice(0, 2)}:${startStr.slice(2)}`;
              widgetLessonEl.textContent = `${subject}`;
              if (subLabel) subLabel.textContent = `Ab ${fmtStart}`;
            } else {
              widgetLessonEl.textContent = 'Feierabend';
              if (subLabel) subLabel.textContent = 'Heute';
            }
          }
        } else {
          widgetLessonEl.textContent = 'Keine Schule';
          if (subLabel) subLabel.textContent = 'Heute';
        }
      }
    }).catch(() => {});
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

  // Auto-hide header on scroll down, reveal on scroll up
  const appEl = document.getElementById('app');
  const headerEl = document.getElementById('dashboard-header');
  let lastScrollY = 0;
  let headerHidden = false;
  appEl.addEventListener('scroll', function() {
    const y = appEl.scrollTop;
    const delta = y - lastScrollY;
    if (delta > 6 && !headerHidden && y > 80) {
      headerEl.style.transform = 'translateY(-110%)';
      headerEl.style.opacity = '0';
      headerEl.style.pointerEvents = 'none';
      headerHidden = true;
    } else if (delta < -6 && headerHidden) {
      headerEl.style.transform = '';
      headerEl.style.opacity = '';
      headerEl.style.pointerEvents = '';
      headerHidden = false;
    }
    lastScrollY = y;
  }, { passive: true });
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
    navigator.serviceWorker.register('/sw.js').then(function(reg) {
      // If a new SW is already waiting right after registration (rare but possible)
      if (reg.waiting) showUpdateBanner(reg.waiting);

      // A new SW downloaded and installed — waiting for activation
      reg.addEventListener('updatefound', function() {
        const newSW = reg.installing;
        if (!newSW) return;
        newSW.addEventListener('statechange', function() {
          if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
            // New version waiting — show banner
            showUpdateBanner(newSW);
          }
        });
      });
    }).catch(function() {});

    // SW sends SW_UPDATED after it claims all clients → reload to get fresh assets
    navigator.serviceWorker.addEventListener('message', function(e) {
      if (e.data && e.data.type === 'SW_UPDATED') {
        window.location.reload();
      }
    });
  }
  await Push.init();
  refreshNotifBadge();
  runIntro();
}

function showUpdateBanner(swWaiting) {
  // Remove any existing banner
  const existing = document.getElementById('update-banner');
  if (existing) existing.remove();

  const banner = document.createElement('div');
  banner.id = 'update-banner';
  banner.innerHTML =
    '<span>🚀 Neue Version verfügbar</span>' +
    '<button id="update-reload-btn">Jetzt aktualisieren</button>';
  document.body.appendChild(banner);
  // Push the whole app down so the banner can never end up hidden behind the
  // header or covered by FABs/composer bars — it stays put until the user
  // actually updates, there is no dismiss-without-updating path.
  document.body.classList.add('has-update-banner');

  requestAnimationFrame(() => banner.classList.add('active'));

  document.getElementById('update-reload-btn').addEventListener('click', function() {
    // Tell the waiting SW to take over
    swWaiting.postMessage({ type: 'SKIP_WAITING' });
    // Reload will be triggered by the SW_UPDATED message above
    setTimeout(() => window.location.reload(), 400);
  });
}

async function refreshNotifBadge() {
  try {
    const { count } = await API.unreadCount();
    const badge = document.getElementById('notif-badge');
    if (badge) badge.style.display = count > 0 ? 'block' : 'none';
  } catch {}
}

function openImpressum() {
  openModal(`
    <div style="color:#1c1b1f;">
      <h2 style="font-size:1.3rem;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px;">
        <i data-lucide="file-text" style="width:24px;height:24px;color:#6750a4;"></i> Impressum & Datenschutz
      </h2>
      <div style="font-size:0.88rem;line-height:1.5;opacity:0.85;max-height:55vh;overflow-y:auto;padding-right:8px;" class="no-scrollbar">
        <h3 style="font-size:1.05rem;font-weight:700;margin:16px 0 6px 0;">Impressum</h3>
        <p><strong>Angaben gemäß § 5 TMG:</strong></p>
        <p style="margin: 4px 0;">Sofia Schulbegleiter PWA</p>
        <p style="margin: 4px 0;">Musterstraße 123<br>12345 Musterstadt</p>
        
        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">Kontakt:</h4>
        <p style="margin: 4px 0;">Telefon: +49 (0) 123 456789</p>
        <p style="margin: 4px 0;">E-Mail: support@sofia.schule</p>
        
        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">Vertretungsberechtigt:</h4>
        <p style="margin: 4px 0;">Max Mustermann (Administrator)</p>
        
        <hr style="border:none;border-top:1px solid rgba(0,0,0,0.1);margin:16px 0;">
        
        <h3 style="font-size:1.05rem;font-weight:700;margin:16px 0 6px 0;">Datenschutzerklärung</h3>
        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">1. Datenschutz auf einen Blick</h4>
        <p style="margin: 4px 0;">Diese App dient ausschließlich als schulischer Begleiter. Alle erhobenen Noten, Termine und Daten werden verschlüsselt in einer lokalen SQLite-Datenbank auf dem Server gespeichert.</p>
        
        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">2. WebUntis-Verbindung</h4>
        <p style="margin: 4px 0;">Wenn du die WebUntis-Integration nutzt, werden deine Zugangsdaten verschlüsselt gespeichert und ausschließlich zur Abfrage des Stundenplans an die offiziellen WebUntis-Server übertragen.</p>
        
        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">3. Push-Benachrichtigungen</h4>
        <p style="margin: 4px 0;">Für Push-Benachrichtigungen wird ein anonymer Token deines Browsers auf unserem Server hinterlegt. Es werden keine personenbezogenen Daten an Drittanbieter-Push-Dienste übertragen.</p>
        
        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">4. Betroffenenrechte</h4>
        <p style="margin: 4px 0;">Du hast jederzeit das Recht auf Auskunft, Berichtigung oder Löschung deiner in der App gespeicherten Daten. Wende dich hierzu an deinen Klassen-Administrator.</p>
      </div>
      <button class="m3-btn-full" onclick="closeModal()" style="margin-top:20px;background:#eaddff;color:#21005d;">Schließen</button>
    </div>
  `);
}

boot();