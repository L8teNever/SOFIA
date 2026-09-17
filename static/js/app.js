// Prevent mobile gesture & double-tap zoom
document.addEventListener('gesturestart', e => e.preventDefault(), { passive: false });
document.addEventListener('gesturechange', e => e.preventDefault(), { passive: false });
document.addEventListener('gestureend', e => e.preventDefault(), { passive: false });

let lastTouchTime = 0;
document.addEventListener('touchend', function(e) {
  const now = Date.now();
  if (now - lastTouchTime <= 300) {
    if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
      e.preventDefault();
    }
  }
  lastTouchTime = now;
}, { passive: false });

document.addEventListener('wheel', function(e) {
  if (e.ctrlKey) e.preventDefault();
}, { passive: false });

document.addEventListener('dblclick', function(e) {
  if (!['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
    e.preventDefault();
  }
});

// PWA Install Prompt Listener
window.deferredInstallPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  window.deferredInstallPrompt = e;
  if (typeof updatePwaInstallState === 'function') {
    updatePwaInstallState();
  }
});
window.addEventListener('appinstalled', () => {
  window.deferredInstallPrompt = null;
  if (typeof updatePwaInstallState === 'function') {
    updatePwaInstallState();
  }
  showToast('Sofia wurde erfolgreich als App installiert!');
});

let currentUser = null;
let currentPage = null;
let pageHistory = [];
let isSelfPopping = false;
let lastDashboardLoadAt = 0;

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

const KNOWN_PAGES = ['calendar','homework','grades','timetable','mealplan','drive','quickshare','settings','admin','notifications','notification-settings','google-sync','chat'];
const pageCache = new Map();

function prefetchPages() {
  KNOWN_PAGES.forEach(name => {
    if (!pageCache.has(name)) {
      fetch('/pages/' + name + '.html')
        .then(res => res.ok ? res.text() : null)
        .then(html => { if (html) pageCache.set(name, html); })
        .catch(() => {});
    }
  });
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

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

// "Today" (or any date) as a local YYYY-MM-DD string. Date.toISOString()
// converts to UTC first, so for the ~1-2 hours right after local midnight
// in Germany (UTC+1/+2) it silently reports YESTERDAY's date instead —
// every "is this today/tomorrow" check across the app used that pattern
// and was wrong during exactly that window. Always use this instead of
// `d.toISOString().slice(0, 10)` for a *local calendar day* string.
function localDateStr(d = new Date()) {
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

// Directional slide transition for a swipeable container (calendar's month
// grid, the timetable's day view, ...): the current content slides out one
// side while the freshly-rendered content slides in from the other, like a
// standard mobile pager, instead of the content just instantly swapping.
// container: the element whose content changes. dir: 1 = forward/next
// (old exits left, new enters from the right), -1 = back/prev (mirrored).
// renderFn: does the actual content update (e.g. sets .innerHTML) — called
// synchronously in between capturing the old frame and animating the new one.
function slideNav(container, dir, renderFn) {
  if (!container) { renderFn(); return; }
  const parent = container.parentElement;
  if (!parent) { renderFn(); return; }
  if (getComputedStyle(parent).position === 'static') parent.style.position = 'relative';

  const rect = container.getBoundingClientRect();
  const ghost = container.cloneNode(true);
  ghost.style.position = 'absolute';
  ghost.style.top = container.offsetTop + 'px';
  ghost.style.left = container.offsetLeft + 'px';
  ghost.style.width = rect.width + 'px';
  ghost.style.margin = '0';
  ghost.style.pointerEvents = 'none';
  ghost.style.zIndex = '1';
  ghost.style.transition = 'transform 0.24s cubic-bezier(0.2,0.7,0.3,1), opacity 0.24s ease';
  parent.appendChild(ghost);

  renderFn();

  container.style.transition = 'none';
  container.style.transform = 'translateX(' + (dir * 28) + 'px)';
  container.style.opacity = '0';
  void container.offsetWidth; // force reflow before re-enabling the transition
  container.style.transition = 'transform 0.24s cubic-bezier(0.2,0.7,0.3,1), opacity 0.24s ease';
  container.style.transform = 'translateX(0)';
  container.style.opacity = '1';

  requestAnimationFrame(() => {
    ghost.style.transform = 'translateX(' + (dir * -28) + 'px)';
    ghost.style.opacity = '0';
  });
  setTimeout(() => {
    ghost.remove();
    container.style.transition = '';
    container.style.transform = '';
  }, 260);
}

// The one place a page header's markup is written. Every page fills in a
// <div id="...page-header slot..."></div> and replaces it with this at
// script-run time (before openPage()'s lucide.createIcons() call, so the
// icons inside still get converted) — never hand-write a .page-header div.
// The back button is the part this exists to guarantee: every page gets
// the same one, wired to the same close behavior, without having to
// remember to include it correctly each time.
function pageHeader({
  title = '',
  titleId = '',
  titleStyle = '',
  subtitle = '',
  subtitleId = '',
  subtitleStyle = '',
  titleHtml = '',    // full override for the title block, for headers too custom for title/subtitle (e.g. calendar's inline expand chevron) — back button + wrapper are still guaranteed
  backAction = '',   // custom onclick JS (e.g. for a manually-managed overlay); omit for the default closePage() via .back-btn
  backClassName = '', // extra class(es) on the back button itself (e.g. a page-local size override)
  trailing = '',     // raw HTML for whatever comes after the title (icon buttons, badges, spacers) — the one flexible slot
  className = '',    // extra class(es) on the .page-header wrapper (e.g. calendar's compact-row variant)
  style = '',        // extra inline style on the wrapper (e.g. a tighter gap)
} = {}) {
  const back = backAction
    ? `<button class="icon-btn${backClassName ? ' ' + backClassName : ''}" onclick="${backAction}" title="Zurück"><i data-lucide="arrow-left" style="width:22px;height:22px;"></i></button>`
    : `<button class="back-btn${backClassName ? ' ' + backClassName : ''}" title="Zurück"><i data-lucide="arrow-left" style="width:22px;height:22px;"></i></button>`;

  const titleBlock = titleHtml || ((subtitle || subtitleId)
    ? `<div style="flex:1;min-width:0;">
        <div${subtitleId ? ` id="${subtitleId}"` : ''} style="font-size:0.72rem;font-weight:700;text-transform:uppercase;letter-spacing:0.1em;opacity:0.5;${subtitleStyle}">${subtitle}</div>
        <span class="page-title"${titleId ? ` id="${titleId}"` : ''} style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;${titleStyle}">${title}</span>
      </div>`
    : `<span class="page-title"${titleId ? ` id="${titleId}"` : ''} style="flex:1;min-width:0;${titleStyle}">${title}</span>`);

  return `<div class="page-header${className ? ' ' + className : ''}"${style ? ` style="${style}"` : ''}>${back}${titleBlock}${trailing}</div>`;
}

// The small "nothing here yet" placeholder — icon, bold one-liner, optional
// dimmer subtitle — repeated near-identically on homework, grades, mealplan
// and notifications with each page free-handing its own icon size/opacity/
// padding. One definition here means all four now visually match exactly,
// and a future page gets the same look by construction instead of by
// someone eyeballing the existing ones. Most callers toggle this element's
// display rather than re-render it, so pass wrapperId to get an addressable
// node (hidden by default — pass hidden:false to start visible).
function emptyState({
  icon = 'inbox',
  title = '',
  titleId = '',
  titleStyle = 'font-weight:600;',
  subtitle = '',
  subtitleId = '',
  subtitleStyle = 'font-size:0.85rem;margin-top:4px;',
  wrapperId = '',
  hidden = true,
} = {}) {
  const titleHtml = (title || titleId)
    ? `<div${titleId ? ` id="${titleId}"` : ''} style="${titleStyle}">${title}</div>`
    : '';
  const subtitleHtml = (subtitle || subtitleId)
    ? `<div${subtitleId ? ` id="${subtitleId}"` : ''} style="${subtitleStyle}">${subtitle}</div>`
    : '';
  return `<div${wrapperId ? ` id="${wrapperId}"` : ''} style="${hidden ? 'display:none;' : ''}text-align:center;padding:60px 20px;opacity:0.4;">
    <i data-lucide="${icon}" style="width:48px;height:48px;margin-bottom:12px;display:block;margin-left:auto;margin-right:auto;"></i>
    ${titleHtml}
    ${subtitleHtml}
  </div>`;
}

// The circular "+" button pinned bottom-right on pages with one obvious
// primary create-action (new event, new homework item, new grade...) —
// same size/position/shadow everywhere via the .fab class, this just saves
// re-typing the icon wrapper and guarantees the icon is always 24px.
function fab({ icon = 'plus', onclick = '', title = '', id = '' } = {}) {
  return `<button class="fab"${id ? ` id="${id}"` : ''} onclick="${onclick}" title="${title}">
    <i data-lucide="${icon}" style="width:24px;height:24px;"></i>
  </button>`;
}

// The "are you sure?" destructive-action modal — content for openModal().
// Was drifting into two different looks (a circle+alert-triangle version
// on homework, a rounded-square+trash-2 version everywhere else) and admin
// used the browser's native confirm() instead of either. One version now,
// content passed as data (message may include simple inline HTML like a
// bolded item name).
function confirmDelete({
  icon = 'trash-2',
  title = 'Wirklich löschen?',
  message = '',
  confirmLabel = 'Löschen',
  onConfirm = '',
} = {}) {
  return `<div style="text-align:center;padding:12px 6px 6px;">
    <div style="width:54px;height:54px;border-radius:18px;background:#ffdad6;color:#ba1a1a;display:flex;align-items:center;justify-content:center;margin:0 auto 16px;">
      <i data-lucide="${icon}" style="width:26px;height:26px;"></i>
    </div>
    <div style="font-size:1.2rem;font-weight:800;margin-bottom:8px;color:#1c1b1f;">${title}</div>
    <div style="font-size:0.92rem;color:#49454f;line-height:1.45;margin-bottom:22px;">${message}</div>
    <div style="display:flex;gap:10px;">
      <button type="button" class="m3-btn-outline" style="flex:1;" onclick="closeModal()">Abbrechen</button>
      <button type="button" class="m3-btn-primary" style="flex:1;background:#ba1a1a;box-shadow:0 2px 8px rgba(186,26,26,0.3);" onclick="${onConfirm}">${confirmLabel}</button>
    </div>
  </div>`;
}

// The Cancel/Save footer row on bottom sheets that have a real two-way
// choice (as opposed to the single full-width .m3-btn-full "just save"
// sheets, which stay as they are — there's nothing to cancel back to
// there). Some pages already used the shared .m3-sheet-actions class for
// this, others hand-typed the identical inline style instead; this closes
// that gap the same way pageHeader() did for headers.
function sheetActions({
  cancelLabel = 'Abbrechen',
  cancelAction = 'closeSheet()',
  saveLabel = 'Speichern',
  saveAction = '',
} = {}) {
  return `<div class="m3-sheet-actions">
    <button type="button" class="m3-btn-outline" style="flex:1;" onclick="${cancelAction}">${cancelLabel}</button>
    <button type="button" class="m3-btn-primary" style="flex:2;" onclick="${saveAction}">${saveLabel}</button>
  </div>`;
}

// The filter/mode toggle row directly under a page-subbar header (Alle/
// Offen/Erledigt, Ganze Woche/Heute/Mo.../Fr, Tag/Woche) — same .chip
// markup and active-state class hand-typed once per button on homework,
// mealplan and timetable. items: [{value, label, active}]; onclick is the
// page's filter function name, called as onclick(value, this) exactly
// like the hand-written versions did.
function chipRow({ items = [], onclick = '', idPrefix = '' } = {}) {
  return items.map(it =>
    `<button onclick="${onclick}('${it.value}',this)" class="chip${it.active ? ' active' : ''}"${idPrefix ? ` id="${idPrefix}-${it.value}"` : ''}>${it.label}</button>`
  ).join('');
}

// A small "..." popover menu (Bearbeiten/Löschen etc.) anchored to the
// button that opened it — same portal-to-body positioning the calendar's
// category filter menu used before this generalized it, so any list item
// (calendar events, homework cards, ...) can get one instead of separate
// pencil/trash icon buttons that don't scale past two actions and take up
// more space on the card. items: [{ icon, label, onclick, danger }] —
// onclick is a real function reference, called with no arguments after
// the menu closes.
let _moreMenuEl = null;
let _moreMenuBtn = null;
let _moreMenuOutsideHandler = null;

function toggleMoreMenu(btn, items) {
  const reopening = _moreMenuBtn === btn;
  closeMoreMenu();
  if (reopening) return;

  const panel = document.createElement('div');
  panel.className = 'm3-dropdown-panel dd-open';
  panel.innerHTML = items.map((it, i) =>
    `<div class="m3-dropdown-opt${it.danger ? ' m3-dropdown-opt-danger' : ''}" data-i="${i}" style="gap:10px;">
      ${it.icon ? `<i data-lucide="${it.icon}" style="width:16px;height:16px;flex-shrink:0;"></i>` : ''}
      <span>${it.label}</span>
    </div>`
  ).join('');
  document.body.appendChild(panel);
  _moreMenuEl = panel;
  _moreMenuBtn = btn;

  const r = btn.getBoundingClientRect();
  panel.style.minWidth = '180px';
  panel.style.left = 'auto';
  panel.style.right = (window.innerWidth - r.right) + 'px';
  const spaceBelow = window.innerHeight - r.bottom - 8;
  if (spaceBelow >= 140 || spaceBelow >= r.top) {
    panel.style.top = (r.bottom + 6) + 'px';
  } else {
    panel.style.bottom = (window.innerHeight - r.top + 6) + 'px';
  }
  // Right-aligning to the button (right edge = button's right edge, panel
  // extends leftward) runs off the left edge of the screen when the button
  // itself sits near the left edge (e.g. chat's leftmost "+" attach
  // button) — clamp back onto screen by switching to a left-pinned
  // position instead of letting the panel get cut off.
  if (panel.getBoundingClientRect().left < 8) {
    panel.style.right = 'auto';
    panel.style.left = '8px';
  }
  if (window.lucide) lucide.createIcons();

  panel.querySelectorAll('.m3-dropdown-opt').forEach(opt => {
    opt.addEventListener('click', (e) => {
      e.stopPropagation();
      const item = items[parseInt(opt.dataset.i, 10)];
      closeMoreMenu();
      if (item.onclick) item.onclick();
    });
  });

  setTimeout(() => {
    _moreMenuOutsideHandler = (e) => {
      if (_moreMenuEl && !_moreMenuEl.contains(e.target) && !btn.contains(e.target)) closeMoreMenu();
    };
    document.addEventListener('click', _moreMenuOutsideHandler);
  }, 0);
}

function closeMoreMenu() {
  if (_moreMenuEl) { _moreMenuEl.remove(); _moreMenuEl = null; }
  if (_moreMenuOutsideHandler) { document.removeEventListener('click', _moreMenuOutsideHandler); _moreMenuOutsideHandler = null; }
  _moreMenuBtn = null;
}

// The "..." button itself — same size/position everywhere a card offers
// this menu instead of separate action icons.
function moreMenuBtn(onclick) {
  return `<button type="button" onclick="event.stopPropagation();${onclick}" class="ev-action-btn more" title="Weitere Aktionen">
    <i data-lucide="more-vertical" style="width:16px;height:16px;"></i>
  </button>`;
}

// Purely visual: shrinks the one card being deleted (found via data-id,
// which every list card carries) out of view, immediately — this never
// waits on the network, so the delete feels instant regardless of
// connection speed, same as the local-first checkbox toggle elsewhere.
// Deliberately doesn't touch any data or trigger a re-render itself —
// an earlier version had this fire onDone() (which removed the item and
// re-rendered) on its own 240ms timer, racing an API-failure handler
// that tried to revert on a *different* timer if the request happened
// to fail faster than that: the animation's timer always ran last and
// silently re-deleted the item the revert had just restored. Callers
// now only mutate their array/re-render once they actually know the
// outcome (on success, the card's own already-collapsed DOM node is
// seamlessly replaced by the fresh, one-shorter render; on failure, a
// plain re-render from the still-untouched array brings it right back —
// no separate undo path needed, since nothing was ever removed from the
// data to begin with).
function animateCardRemoval(container, id) {
  const cardEl = container ? container.querySelector(`[data-id="${id}"]`) : null;
  if (!cardEl) return;
  cardEl.style.overflow = 'hidden';
  cardEl.style.transition = 'opacity 0.22s ease, transform 0.22s ease, max-height 0.22s ease, margin 0.22s ease, padding 0.22s ease';
  cardEl.style.maxHeight = cardEl.offsetHeight + 'px'; // lock current height so max-height has something real to animate from
  requestAnimationFrame(() => {
    cardEl.style.opacity = '0';
    cardEl.style.transform = 'scale(0.96)';
    cardEl.style.maxHeight = '0px';
    cardEl.style.marginTop = '0px'; cardEl.style.marginBottom = '0px';
    cardEl.style.paddingTop = '0px'; cardEl.style.paddingBottom = '0px';
  });
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
      // The anchor requires zero leading whitespace so this only touches
      // genuinely top-level declarations — every page here indents nested
      // code, so a `const`/`let` inside a loop or block always starts with
      // whitespace and is left alone. Matching indented ones too (as an
      // earlier version of this regex did) silently turned per-iteration
      // `const` loop variables into one shared `var`, breaking any closure
      // that captured it (e.g. a day cell's onclick keeps the *last*
      // iteration's date instead of its own).
      s.textContent = old.textContent.replace(/^(?:let|const)\s+/gm, 'var ');
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
  let html = pageCache.get(name);
  if (!html) {
    try {
      const res = await fetch('/pages/' + name + '.html');
      if (!res.ok) throw new Error('not found');
      html = await res.text();
      pageCache.set(name, html);
    } catch {
      showToast('Seite nicht gefunden');
      return;
    }
  }
  // A page ends up living directly under <body> (appendChild below), not
  // inside #page-container — that only ever holds one mid-parse, right
  // before it's moved out. So on any *second* direct subpage-to-subpage
  // navigation (e.g. Settings -> Benachrichtigungen, bypassing the
  // dashboard), this used to find nothing here and never remove the page
  // already showing, leaving two .page.active elements stacked at once.
  // Depending on which one popstate/closePage happened to grab afterward,
  // going back could remove the wrong one and leave neither behind —
  // the blank screen this fixes. :not(.page-overlay) so a still-open
  // detail overlay (its own separate stacking mechanism) is left alone.
  const existing = document.querySelector('body > .page:not(.page-overlay)');
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

  if (!preserveUrl) {
    history.pushState({ page: name }, '', '/' + name);
  } else {
    history.replaceState({ page: name }, '', window.location.pathname);
  }
  pageHistory.push(name);
  currentPage = name;
  if (window.lucide) lucide.createIcons();
  enhanceDropdowns(page);
  enhanceDateTimeInputs(page);
  enhanceInputAutofill(page);

  // Run the page's own data-fetch/render before revealing anything: .page
  // is opacity:0 + pointer-events:none by default regardless of where it
  // sits in the DOM, so it can sit here fully invisible while init_<name>
  // awaits its API calls and fills in the real content. Only once that's
  // done do we hide the outgoing view and animate the new page in — so it
  // never shows a "wird geladen…" placeholder mid-animation, and the
  // outgoing page/dashboard stays live on screen for the whole wait
  // instead of both views going blank at once.
  // Page names can contain hyphens (notification-settings, google-sync) but
  // a JS function name can't, so the page's own init_<name>() is always
  // declared with underscores — normalize here or the lookup silently
  // misses and the page never actually fetches its data.
  const initFn = window['init_' + name.replace(/-/g, '_')];
  if (initFn) {
    const result = initFn();
    if (result && typeof result.then === 'function') await result;
  }

  // Force layout reflow so animation starts immediately
  page.getBoundingClientRect();
  requestAnimationFrame(() => page.classList.add('active'));

  // Completely isolate background dashboard from touch/scroll/selection bleedthrough
  document.body.classList.add('has-active-page');
  const appContainer = document.getElementById('app');
  if (appContainer) {
    appContainer.setAttribute('inert', '');
    appContainer.setAttribute('aria-hidden', 'true');
  }
}

// Shared by closePage() (in-app back button) and the popstate handler
// (browser/gesture back) — both need to pop pageHistory and then either
// restore the dashboard or rebuild the *parent* subpage's DOM. That
// parent page isn't just sitting there waiting to be revealed: when it
// was opened directly from another subpage (not the dashboard — e.g.
// Einstellungen -> Benachrichtigungen), its DOM was already torn down
// the moment the page being closed opened on top of it (see openPage()'s
// "remove the existing page" step). Skipping that rebuild is exactly
// what left a blank screen behind — dashboard not restored (history
// wasn't empty) and the parent page's own markup long gone.
// fromPopstate: true when called from the popstate handler, where the
// browser has already moved to the correct history entry on its own —
// pushing another one there would just add a stray entry on top of it.
function goBackOnePage(fromPopstate) {
  pageHistory.pop();
  const prev = pageHistory[pageHistory.length - 1] || null;
  currentPage = null; // clear before reopening so openPage()'s "already on this page" guard doesn't skip it
  if (!prev) {
    document.body.classList.remove('has-active-page');
    const appContainer = document.getElementById('app');
    if (appContainer) {
      appContainer.removeAttribute('inert');
      appContainer.removeAttribute('aria-hidden');
    }
    // Refresh dashboard widgets, which otherwise kept showing whatever
    // was true at boot (e.g. an "Aufgaben" count from before a task got
    // checked off on the page just left). Throttled: rapidly bouncing
    // between several pages would otherwise re-fire every one of
    // loadDashboard's several API calls each time, easily enough to trip
    // rate limiting for no real benefit — nothing changes twice in 3s.
    const now = Date.now();
    if (now - lastDashboardLoadAt > 3000) {
      lastDashboardLoadAt = now;
      loadDashboard();
    }
    if (!fromPopstate) history.pushState({ page: null }, '', '/');
  } else {
    pageHistory.pop(); // openPage() below re-pushes it — pop first so it isn't duplicated
    openPage(prev, null, true); // preserveUrl: this *is* the back step, not a new forward navigation
  }
}

function closePage() {
  closeSheet(true);
  closeModal(true);
  const page = document.querySelector('.page.active');
  if (!page) return;
  const wasNotifications = currentPage === 'notifications';
  page.classList.remove('active');
  page.classList.add('closing');
  setTimeout(() => page.remove(), 260);
  goBackOnePage(false);
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

  const lbActive = document.getElementById('meal-lightbox');
  if (lbActive && lbActive.style.display !== 'none' && typeof closeMealLightbox === 'function') {
    closeMealLightbox(true);
    return;
  }

  const hwLb = document.getElementById('hw-lightbox');
  if (hwLb && hwLb.style.display !== 'none' && typeof closeHwLightbox === 'function') {
    closeHwLightbox(true);
    return;
  }

  const drivePreview = document.getElementById('drive-preview-lightbox');
  if (drivePreview && drivePreview.style.display !== 'none' && typeof closeDriveFilePreview === 'function') {
    closeDriveFilePreview(true);
    return;
  }

  const chatDetail = document.getElementById('chat-detail-view');
  if (chatDetail && chatDetail.style.display !== 'none' && typeof closeChatConversation === 'function') {
    closeChatConversation(true);
    return;
  }

  // Drive's own folder-navigation history entries (see drivePushHistory()
  // in pages/drive.html) — each one carries the full nav state to restore,
  // rather than this handler having to know anything about Drive's
  // internal folder-depth logic. Must come before the generic "close the
  // whole page" fallback below, or popping back out of a folder would
  // close all of Drive instead of just stepping up one level.
  if (e.state && e.state.page === 'drive' && typeof driveHandlePopstate === 'function') {
    driveHandlePopstate(e.state);
    return;
  }

  // Detail overlays (e.g. a homework item opened on top of the list page)
  // stack on top of a regular .page rather than replacing it — close the
  // overlay first so back-navigation doesn't yank the page underneath it.
  const overlay = document.querySelector('.page-overlay.active');
  if (overlay) {
    overlay.classList.remove('active');
    overlay.classList.add('closing');
    setTimeout(() => overlay.remove(), 600);
    return;
  }

  const page = document.querySelector('.page.active');
  if (page) {
    page.classList.remove('active');
    page.classList.add('closing');
    setTimeout(() => page.remove(), 600);
    goBackOnePage(true);
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

function formatDateGerman(isoStr) {
  if (!isoStr) return '';
  const parts = isoStr.split('-');
  if (parts.length !== 3) return isoStr;
  const y = parseInt(parts[0], 10);
  const m = parseInt(parts[1], 10) - 1;
  const d = parseInt(parts[2], 10);
  const dt = new Date(y, m, d);
  if (isNaN(dt.getTime())) return isoStr;

  const weekdays = ['So.', 'Mo.', 'Di.', 'Mi.', 'Do.', 'Fr.', 'Sa.'];
  const months = ['Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember'];
  
  const now = new Date();
  const todayIso = localDateStr(now);
  const tomorrowIso = localDateStr(new Date(Date.now() + 86400000));

  const wd = weekdays[dt.getDay()];
  const mName = months[m];

  if (isoStr === todayIso) {
    return `Heute (${wd}, ${d}. ${mName})`;
  } else if (isoStr === tomorrowIso) {
    return `Morgen (${wd}, ${d}. ${mName})`;
  }
  return `${wd}, ${d}. ${mName} ${y}`;
}

function formatTimeGerman(timeStr) {
  if (!timeStr) return '';
  return timeStr + ' Uhr';
}

function openDatePicker(options = {}) {
  let scrim = document.getElementById('m3-picker-scrim');
  if (!scrim) {
    scrim = document.createElement('div');
    scrim.id = 'm3-picker-scrim';
    document.body.appendChild(scrim);
  }

  const todayIso = localDateStr();
  const tomorrowIso = localDateStr(new Date(Date.now() + 86400000));
  const nextWeekIso = localDateStr(new Date(Date.now() + 7 * 86400000));

  let selectedIso = options.value && options.value.match(/^\d{4}-\d{2}-\d{2}$/) ? options.value : todayIso;
  let viewYear = parseInt(selectedIso.slice(0, 4), 10);
  let viewMonth = parseInt(selectedIso.slice(5, 7), 10) - 1;

  function close() {
    scrim.classList.remove('active');
    setTimeout(() => { scrim.innerHTML = ''; }, 220);
    document.removeEventListener('keydown', onKeyDown);
  }

  function onKeyDown(e) {
    if (e.key === 'Escape') {
      close();
      if (options.onCancel) options.onCancel();
    }
  }
  document.addEventListener('keydown', onKeyDown);

  function render() {
    const months = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'];
    const firstDay = new Date(viewYear, viewMonth, 1);
    const startWeekday = (firstDay.getDay() + 6) % 7; // 0=Mo ... 6=So
    const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
    const prevMonthDays = new Date(viewYear, viewMonth, 0).getDate();

    let gridHtml = '';
    // Leading days from previous month
    for (let i = startWeekday - 1; i >= 0; i--) {
      gridHtml += `<div class="m3-cal-day-cell muted">${prevMonthDays - i}</div>`;
    }
    // Current month days
    for (let d = 1; d <= daysInMonth; d++) {
      const dStr = String(d).padStart(2, '0');
      const mStr = String(viewMonth + 1).padStart(2, '0');
      const iso = `${viewYear}-${mStr}-${dStr}`;
      const isSel = iso === selectedIso;
      const isToday = iso === todayIso;
      gridHtml += `<button type="button" class="m3-cal-day-cell ${isSel ? 'selected' : ''} ${isToday ? 'today' : ''}" data-date="${iso}">${d}</button>`;
    }
    // Trailing days for neat grid
    const totalCells = startWeekday + daysInMonth;
    const remaining = (7 - (totalCells % 7)) % 7;
    for (let i = 1; i <= remaining; i++) {
      gridHtml += `<div class="m3-cal-day-cell muted">${i}</div>`;
    }

    const headerLabel = formatDateGerman(selectedIso) || 'Datum auswählen';

    scrim.innerHTML = `
      <div class="m3-picker-dialog" onclick="event.stopPropagation()">
        <div class="m3-picker-header">
          <div class="m3-picker-label">${options.title || 'DATUM AUSWÄHLEN'}</div>
          <div class="m3-picker-display">${headerLabel}</div>
        </div>
        <div class="m3-picker-chips">
          <button type="button" class="m3-picker-chip ${selectedIso === todayIso ? 'active' : ''}" id="m3-chip-today">Heute</button>
          <button type="button" class="m3-picker-chip ${selectedIso === tomorrowIso ? 'active' : ''}" id="m3-chip-tomorrow">Morgen</button>
          <button type="button" class="m3-picker-chip ${selectedIso === nextWeekIso ? 'active' : ''}" id="m3-chip-nextweek">+1 Woche</button>
        </div>
        <div class="m3-cal-nav">
          <button type="button" class="m3-cal-btn" id="m3-cal-prev" title="Vorheriger Monat"><svg style="width:18px;height:18px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="15 18 9 12 15 6"/></svg></button>
          <div class="m3-cal-month-title">${months[viewMonth]} ${viewYear}</div>
          <button type="button" class="m3-cal-btn" id="m3-cal-next" title="Nächster Monat"><svg style="width:18px;height:18px;" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"/></svg></button>
        </div>
        <div class="m3-cal-weekdays">
          <span>Mo</span><span>Di</span><span>Mi</span><span>Do</span><span>Fr</span><span>Sa</span><span>So</span>
        </div>
        <div class="m3-cal-grid">
          ${gridHtml}
        </div>
        <div class="m3-picker-actions">
          <button type="button" class="m3-btn-text" id="m3-dp-cancel">Abbrechen</button>
          <button type="button" class="m3-btn-primary" id="m3-dp-confirm">Übernehmen</button>
        </div>
      </div>
    `;

    document.getElementById('m3-cal-prev').onclick = () => {
      viewMonth--;
      if (viewMonth < 0) { viewMonth = 11; viewYear--; }
      render();
    };
    document.getElementById('m3-cal-next').onclick = () => {
      viewMonth++;
      if (viewMonth > 11) { viewMonth = 0; viewYear++; }
      render();
    };
    document.getElementById('m3-chip-today').onclick = () => {
      selectedIso = todayIso;
      viewYear = parseInt(todayIso.slice(0, 4), 10);
      viewMonth = parseInt(todayIso.slice(5, 7), 10) - 1;
      render();
    };
    document.getElementById('m3-chip-tomorrow').onclick = () => {
      selectedIso = tomorrowIso;
      viewYear = parseInt(tomorrowIso.slice(0, 4), 10);
      viewMonth = parseInt(tomorrowIso.slice(5, 7), 10) - 1;
      render();
    };
    document.getElementById('m3-chip-nextweek').onclick = () => {
      selectedIso = nextWeekIso;
      viewYear = parseInt(nextWeekIso.slice(0, 4), 10);
      viewMonth = parseInt(nextWeekIso.slice(5, 7), 10) - 1;
      render();
    };

    scrim.querySelectorAll('.m3-cal-day-cell[data-date]').forEach(btn => {
      btn.onclick = () => {
        selectedIso = btn.dataset.date;
        render();
      };
    });

    document.getElementById('m3-dp-cancel').onclick = () => {
      close();
      if (options.onCancel) options.onCancel();
    };
    document.getElementById('m3-dp-confirm').onclick = () => {
      close();
      if (options.onSelect) options.onSelect(selectedIso);
    };
  }

  scrim.onclick = (e) => {
    if (e.target === scrim) {
      close();
      if (options.onCancel) options.onCancel();
    }
  };

  render();
  scrim.classList.add('active');
}

function openTimePicker(options = {}) {
  let scrim = document.getElementById('m3-picker-scrim');
  if (!scrim) {
    scrim = document.createElement('div');
    scrim.id = 'm3-picker-scrim';
    document.body.appendChild(scrim);
  }

  let selectedHours = 8;
  let selectedMinutes = 0;
  if (options.value && options.value.includes(':')) {
    const parts = options.value.split(':').map(Number);
    if (!isNaN(parts[0])) selectedHours = parts[0];
    if (!isNaN(parts[1])) selectedMinutes = parts[1];
  }
  let activeTab = 'hours'; // 'hours' | 'minutes'

  const schoolPresets = [
    { label: '1. Stunde', time: '07:50' },
    { label: '2. Stunde', time: '08:40' },
    { label: '3. Stunde', time: '09:45' },
    { label: '4. Stunde', time: '10:35' },
    { label: '5. Stunde', time: '11:40' },
    { label: '6. Stunde', time: '12:30' },
    { label: '7. Stunde', time: '13:30' },
    { label: '8. Stunde', time: '14:15' }
  ];

  function close() {
    scrim.classList.remove('active');
    setTimeout(() => { scrim.innerHTML = ''; }, 220);
    document.removeEventListener('keydown', onKeyDown);
  }

  function onKeyDown(e) {
    if (e.key === 'Escape') {
      close();
      if (options.onCancel) options.onCancel();
    }
  }
  document.addEventListener('keydown', onKeyDown);

  function render() {
    const hhStr = String(selectedHours).padStart(2, '0');
    const mmStr = String(selectedMinutes).padStart(2, '0');
    const currentTime = `${hhStr}:${mmStr}`;

    let presetsHtml = '';
    for (const p of schoolPresets) {
      const isAct = p.time === currentTime;
      presetsHtml += `
        <button type="button" class="m3-tp-preset-chip ${isAct ? 'active' : ''}" data-time="${p.time}">
          <span>${p.time}</span>
          <span>${p.label}</span>
        </button>
      `;
    }

    let digitsHtml = '';
    if (activeTab === 'hours') {
      const hoursList = [7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,0,1,2,3,4,5,6];
      digitsHtml = hoursList.map(h => {
        const str = String(h).padStart(2, '0');
        const isAct = h === selectedHours;
        return `<button type="button" class="m3-tp-digit-btn ${isAct ? 'active' : ''}" data-hour="${h}">${str}</button>`;
      }).join('');
    } else {
      const minutesList = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55];
      digitsHtml = minutesList.map(m => {
        const str = String(m).padStart(2, '0');
        const isAct = m === selectedMinutes;
        return `<button type="button" class="m3-tp-digit-btn ${isAct ? 'active' : ''}" data-minute="${m}">${str}</button>`;
      }).join('');
    }

    scrim.innerHTML = `
      <div class="m3-picker-dialog" onclick="event.stopPropagation()">
        <div class="m3-picker-header" style="text-align:center;">
          <div class="m3-picker-label">${options.title || 'UHRZEIT AUSWÄHLEN'}</div>
          <div class="m3-time-boxes">
            <button type="button" class="m3-time-box ${activeTab === 'hours' ? 'active' : ''}" id="m3-tb-hh">${hhStr}</button>
            <span class="m3-time-colon">:</span>
            <button type="button" class="m3-time-box ${activeTab === 'minutes' ? 'active' : ''}" id="m3-tb-mm">${mmStr}</button>
          </div>
        </div>

        <div class="m3-tp-section-title">Typische Schulzeiten</div>
        <div class="m3-tp-presets-grid">
          ${presetsHtml}
        </div>

        <div class="m3-tp-section-title">${activeTab === 'hours' ? 'Stunde auswählen' : 'Minute auswählen'}</div>
        <div class="m3-tp-digits-grid no-scrollbar">
          ${digitsHtml}
        </div>

        <div class="m3-picker-actions">
          ${options.optional !== false ? '<button type="button" class="m3-btn-text" id="m3-tp-clear" style="margin-right:auto;color:#ba1a1a;">Keine Zeit</button>' : ''}
          <button type="button" class="m3-btn-text" id="m3-tp-cancel">Abbrechen</button>
          <button type="button" class="m3-btn-primary" id="m3-tp-confirm">Übernehmen</button>
        </div>
      </div>
    `;

    document.getElementById('m3-tb-hh').onclick = () => { activeTab = 'hours'; render(); };
    document.getElementById('m3-tb-mm').onclick = () => { activeTab = 'minutes'; render(); };

    scrim.querySelectorAll('.m3-tp-preset-chip[data-time]').forEach(chip => {
      chip.onclick = () => {
        const parts = chip.dataset.time.split(':').map(Number);
        selectedHours = parts[0];
        selectedMinutes = parts[1];
        render();
      };
    });

    scrim.querySelectorAll('.m3-tp-digit-btn[data-hour]').forEach(btn => {
      btn.onclick = () => {
        selectedHours = parseInt(btn.dataset.hour, 10);
        activeTab = 'minutes';
        render();
      };
    });

    scrim.querySelectorAll('.m3-tp-digit-btn[data-minute]').forEach(btn => {
      btn.onclick = () => {
        selectedMinutes = parseInt(btn.dataset.minute, 10);
        render();
      };
    });

    const clearBtn = document.getElementById('m3-tp-clear');
    if (clearBtn) {
      clearBtn.onclick = () => {
        close();
        if (options.onSelect) options.onSelect('');
      };
    }
    document.getElementById('m3-tp-cancel').onclick = () => {
      close();
      if (options.onCancel) options.onCancel();
    };
    document.getElementById('m3-tp-confirm').onclick = () => {
      close();
      if (options.onSelect) options.onSelect(currentTime);
    };
  }

  scrim.onclick = (e) => {
    if (e.target === scrim) {
      close();
      if (options.onCancel) options.onCancel();
    }
  };

  render();
  scrim.classList.add('active');
}

// Every .m3-input suppresses the browser's own autofill suggestions —
// without an explicit autocomplete value, Chrome/Gboard sometimes shows
// its password/payment/address icon strip above the keyboard for a
// plain "Note" or "Titel" field that has nothing to do with saved
// browser data (this app has no login/payment/address forms these
// fields could plausibly match anyway). Applied here, alongside the
// other input enhancers, instead of by hand on every single <input> —
// a newly added field gets this by construction too.
function enhanceInputAutofill(root = document) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll('.m3-input:not([autocomplete])').forEach(input => {
    input.setAttribute('autocomplete', 'off');
  });
}

function enhanceDateTimeInputs(root = document) {
  if (!root || !root.querySelectorAll) return;
  const inputs = root.querySelectorAll('input[type="date"], input[type="time"]');

  inputs.forEach(input => {
    if (input.dataset.m3Enhanced === 'true') return;
    input.dataset.m3Enhanced = 'true';

    const isDate = input.type === 'date';
    input.style.display = 'none';

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'm3-dt-trigger';
    if (input.id) trigger.id = input.id + '-trigger';

    const iconSvg = isDate
      ? '<svg class="m3-dt-trigger-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>'
      : '<svg class="m3-dt-trigger-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>';

    const chevSvg = '<svg class="m3-dt-trigger-chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>';

    trigger.innerHTML = `
      <div class="m3-dt-trigger-left">
        ${iconSvg}
        <span class="m3-dt-trigger-text"></span>
      </div>
      ${chevSvg}
    `;

    input.parentNode.insertBefore(trigger, input.nextSibling);

    const textSpan = trigger.querySelector('.m3-dt-trigger-text');
    function syncLabel() {
      const val = input.value;
      if (isDate) {
        if (val) {
          textSpan.textContent = formatDateGerman(val);
          textSpan.style.opacity = '1';
        } else {
          textSpan.textContent = input.placeholder || 'Datum auswählen';
          textSpan.style.opacity = '0.55';
        }
      } else {
        if (val) {
          textSpan.textContent = val + ' Uhr';
          textSpan.style.opacity = '1';
        } else {
          textSpan.textContent = input.placeholder || 'Uhrzeit (optional)';
          textSpan.style.opacity = '0.55';
        }
      }
    }
    syncLabel();

    // Intercept .value setter so programmatic changes immediately update the label
    const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    if (desc && desc.set) {
      Object.defineProperty(input, 'value', {
        get() { return desc.get.call(this); },
        set(newVal) {
          desc.set.call(this, newVal);
          syncLabel();
        },
        configurable: true
      });
    }

    input.addEventListener('change', syncLabel);
    input.addEventListener('input', syncLabel);

    trigger.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (isDate) {
        openDatePicker({
          value: input.value,
          title: input.getAttribute('placeholder') || 'Datum auswählen',
          onSelect: (newVal) => {
            input.value = newVal;
            syncLabel();
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            if (typeof input.onchange === 'function') input.onchange();
          }
        });
      } else {
        openTimePicker({
          value: input.value,
          title: input.getAttribute('placeholder') || 'Uhrzeit auswählen',
          optional: true,
          onSelect: (newVal) => {
            input.value = newVal;
            syncLabel();
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            if (typeof input.onchange === 'function') input.onchange();
          }
        });
      }
    });
  });
}

function openSheet(html) {
  let sheet = document.querySelector('.bottom-sheet');
  if (!sheet) { sheet = document.createElement('div'); sheet.className = 'bottom-sheet'; document.body.appendChild(sheet); }
  sheet.innerHTML = '<div class="sheet-handle"></div>' + html;
  enhanceDropdowns(sheet);
  enhanceDateTimeInputs(sheet);
  enhanceInputAutofill(sheet);
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
  enhanceDateTimeInputs(document.getElementById('modal-content'));
  enhanceInputAutofill(document.getElementById('modal-content'));
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
    const today = localDateStr();
    const openHw = hw.filter(h => h.due_date >= today && !(h.checked_by || []).includes(currentUser && currentUser.id));
    document.getElementById('w-hw-count').textContent = openHw.length;
    const upcoming = events.filter(e => e.date >= today).sort((a, b) => a.date.localeCompare(b.date));
    if (upcoming.length) {
      const ev = upcoming[0];
      const d = new Date(ev.date + 'T00:00');
      const isTomorrow = ev.date === localDateStr(new Date(Date.now()+86400000));
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

    API.chatConversations().then(convs => {
      const unread = convs.reduce((sum, c) => sum + (c.is_muted ? 0 : (c.unread_count || 0)), 0);
      const badge = document.getElementById('w-chat-badge');
      if (badge) {
        badge.textContent = unread > 99 ? '99+' : unread;
        badge.style.display = unread > 0 ? 'flex' : 'none';
      }
      const preview = document.getElementById('w-chat-preview');
      const sub = document.getElementById('w-chat-preview-sub');
      if (preview && sub) {
        const latest = convs[0]; // already sorted newest-first by the API
        if (latest && latest.last_message) {
          const title = latest.is_group ? latest.name : (latest.participants.find(p => !currentUser || p.user_id !== currentUser.id)?.display_name || '?');
          preview.textContent = latest.last_message;
          sub.textContent = title || '';
        } else {
          preview.textContent = 'Keine Nachrichten';
          sub.textContent = '';
        }
      }
    }).catch(() => {});

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

    // Fetch and update Essensplan widget
    API.mealplanCurrent().then(res => {
      const el = document.getElementById('w-meal-today');
      const sub = document.getElementById('w-meal-sub');
      if (!el) return;

      const dayOfWeek = new Date().getDay();
      if (dayOfWeek === 0 || dayOfWeek === 6) {
        if (sub) sub.textContent = 'Mensa';
        el.textContent = 'Wochenende';
        return;
      }
      if (!res || !res.today || !res.today.meal) {
        if (sub) sub.textContent = 'Heute';
        el.textContent = 'Kein Essen eingetragen';
        return;
      }

      const lines = res.today.meal.split('\n').map(l => l.trim()).filter(Boolean);
      let mainDish = null;
      for (const l of lines) {
        const m = l.match(/^(?:hauptgericht|menü|menü 1):\s*(.*)/i);
        if (m) { mainDish = m[1]; break; }
      }
      if (!mainDish && lines.length > 0) {
        mainDish = lines[0].replace(/^[^:]+:\s*/, '');
      }

      if (sub) sub.textContent = 'Heute';
      el.textContent = mainDish || 'Kein Plan';
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

let introTimer = null;
let introFinished = false;

function skipIntro() {
  if (introFinished) return;
  introFinished = true;
  if (introTimer) clearTimeout(introTimer);
  const overlay = document.getElementById('intro-overlay');
  if (overlay) {
    overlay.style.transition = 'opacity 0.22s ease';
    overlay.style.opacity = '0';
    overlay.style.pointerEvents = 'none';
    setTimeout(function() { if (overlay && overlay.parentNode) overlay.remove(); }, 250);
  }
  showApp();
}

function runIntro(deepLinkPromise) {
  const overlay = document.getElementById('intro-overlay');
  if (overlay) {
    overlay.addEventListener('click', skipIntro);
    overlay.addEventListener('touchstart', skipIntro, { passive: true });
  }

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
  introTimer = setTimeout(async function() {
    if (introFinished) return;
    if (deepLinkPromise) { try { await deepLinkPromise; } catch (e) {} }
    skipIntro();
  }, charIdx * 45 + 700);
}

function showApp() {
  document.getElementById('app').classList.add('ready');
  document.querySelectorAll('.widget').forEach(function(w, i) {
    setTimeout(function() { w.classList.add('pop'); }, i * 35);
  });
  if (window.lucide) lucide.createIcons();
  loadDashboard();
  // A deep link straight to a subpage (e.g. reloading on /homework) is
  // already opened by now — kicked off back in boot(), before the intro
  // even finished — so there's nothing to auto-open here anymore; see
  // runIntro()/boot() for that.

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
    prefetchPages();
  } catch (err) {
    let email = '';
    try { const c = await fetch('/api/v1/auth/check'); const j = await c.json(); email = j.email || ''; } catch {}
    showNotActivated(email);
    return;
  }

  // If we're landing directly on a known subpage's URL (e.g. reloading on
  // /homework), start opening it now instead of waiting until after the
  // intro animation and the dashboard's own reveal — it renders behind the
  // intro overlay (z-index 100 vs. the overlay's 2000), so starting early
  // is invisible either way, and it gives the fetch the whole rest of
  // boot() + the intro animation to finish before anything becomes visible.
  const urlPage = window.location.pathname.replace(/^\//, '').split('/')[0];
  const deepLinkPromise = (urlPage && KNOWN_PAGES.includes(urlPage)) ? openPage(urlPage, null, true) : null;

  const didOnboard = await checkAndRunOnboarding();
  updateGreeting();
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

      // The browser only re-fetches sw.js on its own when the page navigates,
      // so a tab left open for a while would never notice a new deploy until
      // the user manually reloads. Poll for updates ourselves so the banner
      // shows up while the app is still open, not just after a reload.
      setInterval(function() { reg.update().catch(function() {}); }, 60 * 1000);
      document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible') reg.update().catch(function() {});
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
  runIntro(deepLinkPromise);
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

async function openImpressum() {
  let cfg = {
    business_name: "Sofia Schulbegleiter PWA",
    address: "Musterstraße 123<br>12345 Musterstadt",
    phone: "+49 (0) 123 456789",
    email: "support@sofia.schule",
    name: "Max Mustermann",
  };
  try {
    const res = await fetch('/api/v1/config/impressum');
    if (res.ok) cfg = { ...cfg, ...(await res.json()) };
  } catch {}

  openModal(`
    <div style="color:#1c1b1f;">
      <h2 style="font-size:1.3rem;font-weight:700;margin-bottom:12px;display:flex;align-items:center;gap:8px;">
        <i data-lucide="file-text" style="width:24px;height:24px;color:#6750a4;"></i> Impressum & Datenschutz
      </h2>
      <div style="font-size:0.88rem;line-height:1.5;opacity:0.85;max-height:55vh;overflow-y:auto;padding-right:8px;" class="no-scrollbar">
        <h3 style="font-size:1.05rem;font-weight:700;margin:16px 0 6px 0;">Impressum</h3>
        <p><strong>Angaben gemäß § 5 TMG:</strong></p>
        <p style="margin: 4px 0;">${cfg.business_name}</p>
        <p style="margin: 4px 0;">${cfg.address}</p>

        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">Kontakt:</h4>
        <p style="margin: 4px 0;">Telefon: ${cfg.phone}</p>
        <p style="margin: 4px 0;">E-Mail: ${cfg.email}</p>

        <h4 style="font-size:0.92rem;font-weight:700;margin:12px 0 4px 0;">Vertretungsberechtigt:</h4>
        <p style="margin: 4px 0;">${cfg.name} (Administrator)</p>

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