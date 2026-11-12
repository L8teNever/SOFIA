// Design: "system" (folgt dem Geraet, Standard), "light" oder "dark".
// Laeuft frueh im <head>, damit beim Laden nichts aufblitzt.
(function () {
  var KEY = 'sofia-theme';
  var mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function getPref() {
    var v = null;
    try { v = localStorage.getItem(KEY); } catch (e) {}
    return v === 'light' || v === 'dark' ? v : 'system';
  }
  function isDark(pref) {
    return pref === 'dark' || (pref === 'system' && !!(mq && mq.matches));
  }
  function apply() {
    var dark = isDark(getPref());
    document.documentElement.classList.toggle('dark', dark);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', dark ? '#1c1b1f' : '#6750a4');
    document.dispatchEvent(new CustomEvent('themechange', { detail: { dark: dark, pref: getPref() } }));
  }
  window.getThemePref = getPref;
  window.setThemePref = function (pref) {
    try {
      if (pref === 'light' || pref === 'dark') localStorage.setItem(KEY, pref);
      else localStorage.removeItem(KEY);
    } catch (e) {}
    apply();
  };
  if (mq) {
    if (mq.addEventListener) mq.addEventListener('change', apply);
    else if (mq.addListener) mq.addListener(apply);
  }
  // Klasse sofort setzen (meta existiert evtl. noch nicht -> apply() nochmal nach dem Parsen)
  document.documentElement.classList.toggle('dark', isDark(getPref()));
  document.addEventListener('DOMContentLoaded', apply);
})();
