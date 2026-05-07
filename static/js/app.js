// Global state
let currentUser = null;
let currentPage = null;
let pageHistory = [];

// ── Toast ──────────────────────────────────────────────
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

// ── Page Router ────────────────────────────────────────
async function openPage(name, triggerEl) {
  if (currentPage === name) return;

  // Calculate transform origin from widget position
  let ox = '50%', oy = '50%';
  if (triggerEl) {
    const r = triggerEl.getBoundingClientRect();
    ox = (r.left + r.width / 2) + 'px';
    oy = (r.top + r.height / 2) + 'px';
  }

  // Fetch page partial
  let html;
  try {
    const res = await fetch(`/pages/${name}.html`);
    html = await res.text();
  } catch {
    showToast('Seite nicht gefunden');
    return;
  }

  // Remove existing page if any
  const existing = document.getElementById('page-container').querySelector('.page');
  if (existing) existing.remove();

  const container = document.getElementById('page-container');
  container.innerHTML = html;
  const page = container.querySelector('.page') || container.firstElementChild;
  if (!page) return;
  page.style.setProperty('--ox', ox);
  page.style.setProperty('--oy', oy);
  document.body.appendChild(page);
  container.innerHTML = '';

  // Trigger animation
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      page.classList.add('active');
    });
  });

  history.pushState({ page: name }, '', '/' + name);
  pageHistory.push(name);
  currentPage = name;

  // Init icons and page script
  if (window.lucide) lucide.createIcons();
  const initFn = window[`init_${name}`];
  if (initFn) initFn();
}

function closePage() {
  const page = document.querySelector('.page.active');
  if (!page) return;
  page.classList.remove('active');
  page.classList.add('closing');
  setTimeout(() => page.remove(), 600);
  pageHistory.pop();
  currentPage = pageHistory[pageHistory.length - 1] || null;
  const prev = pageHistory[pageHistory.length - 1];
  history.pushState({ page: prev }, '', prev ? '/' + prev : '/');
}

// Back button — global handler
document.addEventListener('click', e => {
  if (e.target.closest('.back-btn')) closePage();
});

// Browser back
window.addEventListener('popstate', e => {
  const page = document.querySelector('.page.active');
  if (page) { page.classList.remove('active'); page.classList.add('closing'); setTimeout(() => page.remove(), 600); currentPage = null; }
});

// ── Bottom Sheet ───────────────────────────────────────
function openSheet(html) {
  let sheet = document.querySelector('.bottom-sheet');
  if (!sheet) { sheet = document.createElement('div'); sheet.className = 'bottom-sheet'; document.body.appendChild(sheet); }
  sheet.innerHTML = `<div class="sheet-handle"></div>${html}`;
  document.getElementById('bottom-sheet-scrim').classList.add('active');
  requestAnimationFrame(() => sheet.classList.add('active'));
  if (window.lucide) lucide.createIcons();
}
function closeSheet() {
  const sheet = document.querySelector('.bottom-sheet');
  if (sheet) { sheet.classList.remove('active'); setTimeout(() => sheet.remove(), 400); }
  document.getElementById('bottom-sheet-scrim').classList.remove('active');
}
document.getElementById('bottom-sheet-scrim').addEventListener('click', closeSheet);

// ── Modal ──────────────────────────────────────────────
function openModal(html) {
  document.getElementById('modal-content').innerHTML = html;
  document.getElementById('modal-scrim').classList.add('active');
  if (window.lucide) lucide.createIcons();
}
function closeModal() { document.getElementById('modal-scrim').classList.remove('active'); }
document.getElementById('modal-scrim').addEventListener('click', e => { if (e.target === document.getElementById('modal-scrim')) closeModal(); });

// ── Dashboard Data ─────────────────────────────────────
async function loadDashboard() {
  try {
    const [hw, events, grades, files] = await Promise.all([
      API.homework().catch(() => []),
      API.events().catch(() => []),
      API.grades().catch(() => []),
      API.files().catch(() => []),
    ]);

    // Homework count (open)
    const today = new Date().toISOString().slice(0, 10);
    const openHw = hw.filter(h => h.due_date >= today && !h.checked_by.includes(currentUser?.id));
    document.getElementById('w-hw-count').textContent = openHw.length;

    // Next event
    const upcoming = events.filter(e => e.date >= today).sort((a,b) => a.date.localeCompare(b.date));
    if (upcoming.length) {
      const e = upcoming[0];
      const d = new Date(e.date);
      document.getElementById('w-next-event').textContent = `${e.title} · ${d.toLocaleDateString('de-DE', {day:'numeric',month:'short'})}`;
    }

    // Grade avg
    if (grades.length) {
      const avg = (grades.reduce((s,g) => s+g.value, 0) / grades.length).toFixed(1);
      document.getElementById('w-grade-avg').textContent = avg;
    }

    // Files
    document.getElementById('w-files-count').textContent = files.length;

  } catch {}
}

function updateGreeting() {
  const now = new Date();
  const h = now.getHours();
  const greeting = h < 12 ? 'Guten Morgen' : h < 18 ? 'Guten Tag' : 'Guten Abend';
  const name = currentUser?.display_name || currentUser?.email?.split('@')[0] || '';
  document.getElementById('greeting-name').textContent = `${greeting}${name ? ', ' + name : ''}!`;
  document.getElementById('greeting-date').textContent = now.toLocaleDateString('de-DE', { weekday:'long', day:'numeric', month:'long' });
}

// ── Intro animation ────────────────────────────────────
function runIntro() {
  const text = 'Sofia – Dein Schulbegleiter';
  const container = document.getElementById('intro-text');
  const words = text.split(' ');
  let charIdx = 0;
  words.forEach(word => {
    const wrap = document.createElement('span');
    wrap.className = 'word-wrap';
    [...word].forEach(ch => {
      const span = document.createElement('span');
      span.className = 'flying-char';
      span.textContent = ch;
      span.style.transitionDelay = `${charIdx * 40}ms`;
      wrap.appendChild(span);
      charIdx++;
    });
    container.appendChild(wrap);
    container.appendChild(document.createTextNode(' '));
  });

  requestAnimationFrame(() => {
    document.querySelectorAll('.flying-char').forEach(c => {
      c.style.opacity = '1';
      c.style.transform = 'scale(1) translateY(0)';
    });
  });

  setTimeout(() => {
    const overlay = document.getElementById('intro-overlay');
    overlay.classList.add('hidden');
    setTimeout(() => overlay.remove(), 900);
    showApp();
  }, Math.max(1000, charIdx * 40 + 600));
}

function showApp() {
  const app = document.getElementById('app');
  app.classList.add('ready');
  document.querySelectorAll('.widget').forEach((w, i) => {
    setTimeout(() => w.classList.add('pop'), i * 70);
  });
  if (window.lucide) lucide.createIcons();
  loadDashboard();
}

// ── Boot ───────────────────────────────────────────────
async function boot() {
  try {
    currentUser = await API.me();
  } catch {
    // Not activated or not logged in — show overlay message
    document.getElementById('intro-overlay').innerHTML = `
      <div style="text-align:center;padding:40px;">
        <div style="font-size:2rem;font-weight:700;color:#21005d;margin-bottom:12px;">Kein Zugang</div>
        <div style="color:#49454f;">Dein Konto wurde noch nicht freigeschaltet.<br>Bitte wende dich an den Administrator.</div>
      </div>`;
    return;
  }

  if (currentUser.role === 'admin' || currentUser.role === 'super_admin') {
    document.getElementById('admin-widget').style.display = 'flex';
  }

  updateGreeting();

  // Register SW
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
  }

  await Push.init();
  runIntro();
}

boot();
