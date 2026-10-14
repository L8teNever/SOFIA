// Essensplan zum Anschauen. Alles wird per textContent gesetzt - nie als HTML.
(function () {
  "use strict";
  var DAYS = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
  var MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
  var ICONS = {
    soup: "M4 11h16a8 8 0 0 1-16 0zM8 4c0 1.5 1 1.5 1 3M12 4c0 1.5 1 1.5 1 3M16 4c0 1.5 1 1.5 1 3",
    menu1: "M7 3v8a2 2 0 0 0 2 2v8M7 3v5M11 3v5M5 3v5a2 2 0 0 0 2 2M17 3c-2 2-2 6 0 8v10",
    menu2: "M7 3v8a2 2 0 0 0 2 2v8M7 3v5M11 3v5M5 3v5a2 2 0 0 0 2 2M17 3c-2 2-2 6 0 8v10",
    dessert: "M7 11a5 5 0 0 1 10 0zM7 11l5 10 5-10",
    other: "M4 12h16M12 4v16",
  };

  // Name aus dem Link (?name=...): nur Buchstaben, Leerzeichen, - und ', hoechstens 30 Zeichen
  function nameFromUrl() {
    var raw = "";
    try { raw = new URLSearchParams(location.search).get("name") || ""; } catch (e) {}
    var clean = raw.normalize("NFC").replace(/[^\p{L}\p{M} '\-]/gu, "").replace(/\s+/g, " ").trim().slice(0, 30);
    return clean;
  }
  var name = nameFromUrl();
  document.getElementById("hello").textContent = name ? "Willkommen, " + name : "Willkommen";

  var now = new Date();
  document.getElementById("today").textContent = DAYS[now.getDay()] + ", " + now.getDate() + ". " + MONTHS[now.getMonth()];

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(kind) {
    var ns = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    var p = document.createElementNS(ns, "path");
    p.setAttribute("d", ICONS[kind] || ICONS.other);
    svg.appendChild(p);
    return svg;
  }
  function parseDate(iso) {
    var a = String(iso).split("-");
    return new Date(+a[0], +a[1] - 1, +a[2]);
  }

  var data = null, current = 0, loaded = false;
  var chips = document.getElementById("chips"), card = document.getElementById("card");

  function renderDay(i) {
    current = i;
    Array.prototype.forEach.call(chips.children, function (c, j) { c.classList.toggle("on", j === i); });
    card.textContent = "";
    var d = data.days[i];
    var dt = parseDate(d.date);
    var head = el("div", "day-head");
    head.appendChild(el("b", "", DAYS[dt.getDay()]));
    head.appendChild(el("span", "", dt.getDate() + ". " + MONTHS[dt.getMonth()]));
    if (d.date === data.today) head.appendChild(el("span", "badge", "Heute"));
    card.appendChild(head);
    if (!d.courses.length) { card.appendChild(el("div", "empty", "Für diesen Tag steht nichts im Plan.")); return; }
    d.courses.forEach(function (c) {
      var row = el("div", "row k-" + (ICONS[c.kind] ? c.kind : "other"));
      var ic = el("div", "ico");
      ic.appendChild(icon(c.kind));
      var body = el("div", "");
      body.appendChild(el("div", "lbl", c.label));
      body.appendChild(el("div", "txt", c.text));
      row.appendChild(ic);
      row.appendChild(body);
      card.appendChild(row);
    });
  }

  function render() {
    chips.textContent = "";
    if (!data || !data.days || !data.days.length) {
      card.textContent = "";
      card.appendChild(el("div", "empty", data && data.error ? "Der Essensplan ist gerade nicht erreichbar." : "Für diese Woche gibt es noch keinen Essensplan."));
      return;
    }
    var start = 0;
    data.days.forEach(function (d, i) {
      var dt = parseDate(d.date);
      var b = el("button", "chip", DAYS[dt.getDay()].slice(0, 2));
      b.type = "button";
      b.title = DAYS[dt.getDay()] + ", " + dt.getDate() + ". " + MONTHS[dt.getMonth()];
      if (d.date === data.today) { b.classList.add("today"); start = i; }
      else if (d.date < data.today && start === i) start = i + 1;
      b.addEventListener("click", function () { renderDay(i); });
      chips.appendChild(b);
    });
    renderDay(loaded ? Math.min(current, data.days.length - 1) : Math.min(start, data.days.length - 1));
    loaded = true;
  }

  // Wischen wie in Sofia: Karte folgt dem Finger, nach links = naechster Tag
  (function swipe() {
    var x0 = 0, y0 = 0, dx = 0, active = false, horiz = null;
    function setX(x, fade) {
      card.style.transform = x ? "translateX(" + x + "px)" : "";
      card.style.opacity = fade ? String(Math.max(0.35, 1 - Math.abs(x) / 500)) : "";
    }
    card.addEventListener("touchstart", function (e) {
      if (!data || !data.days || data.days.length < 2 || e.touches.length !== 1) return;
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; dx = 0; active = true; horiz = null;
      card.classList.remove("anim");
    }, { passive: true });
    card.addEventListener("touchmove", function (e) {
      if (!active) return;
      var mx = e.touches[0].clientX - x0, my = e.touches[0].clientY - y0;
      if (horiz === null && (Math.abs(mx) > 8 || Math.abs(my) > 8)) horiz = Math.abs(mx) > Math.abs(my);
      if (!horiz) return;
      dx = mx;
      var atEdge = (dx > 0 && current === 0) || (dx < 0 && current === data.days.length - 1);
      setX(atEdge ? dx * 0.3 : dx, true);
    }, { passive: true });
    function end() {
      if (!active) return;
      active = false;
      card.classList.add("anim");
      var next = current + (dx < -60 ? 1 : dx > 60 ? -1 : 0);
      if (!horiz || next === current || next < 0 || next >= data.days.length) { setX(0); return; }
      var w = card.offsetWidth + 40;
      setX(dx < 0 ? -w : w, true);
      setTimeout(function () {
        card.classList.remove("anim");
        renderDay(next);
        setX(dx < 0 ? w : -w, true);
        requestAnimationFrame(function () { requestAnimationFrame(function () { card.classList.add("anim"); setX(0); }); });
      }, 200);
    }
    card.addEventListener("touchend", end);
    card.addEventListener("touchcancel", end);
  })();

  function load() {
    fetch("/api/plan", { cache: "no-store", credentials: "omit" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { data = d || { days: [], error: true }; render(); })
      .catch(function () { data = { days: [], error: true }; render(); });
  }
  load();
  // alle 15 Minuten neu, falls die Seite lange offen bleibt
  setInterval(load, 15 * 60 * 1000);
})();
