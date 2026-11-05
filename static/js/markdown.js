// Markdown fuer Beschreibungen: sichere Anzeige (renderMarkdown), Einzeiler fuer Karten
// (markdownPlain) und ein Textfeld, das Markdown beim Tippen direkt umwandelt (data-markdown).
// Bewusst ohne Lookbehind in Regexen: aeltere iPads wuerden sonst die ganze Datei nicht laden.
(function (root) {
  'use strict';

  const INDENT = '  ';
  const LIST = /^([ \t]*)([-*+•]|\d{1,9}[.)])([ \t]+)(\[[ xX]\][ \t]+)?(.*)$/;

  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }
  function indentOf(ws) {
    return ws.replace(/\t/g, '    ').length;
  }

  // ---- Anzeige ----
  function safeUrl(url) {
    const u = String(url).trim();
    if (/^(https?:\/\/|mailto:)/i.test(u)) return u;
    if (/^www\.[^\s]+$/i.test(u)) return 'https://' + u;
    return null;
  }
  function link(url, html) {
    return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${html}</a>`;
  }
  // live = im Textfeld: Backslash-Zeichen und nackte Adressen bleiben genau so stehen, wie sie
  // getippt sind - sonst gingen sie beim Zurueckschreiben verloren.
  function inline(src, live) {
    const slots = [];
    const keep = (html) => {
      slots.push(html);
      return `\u0000${slots.length - 1}\u0000`;
    };
    let s = String(src).replace(/\u0000/g, '');
    s = s.replace(/\\([\\`*_~[\]()#+\-.!>])/g, (all, c) => keep(esc(live ? all : c)));
    s = s.replace(/`([^`\n]+)`/g, (_, c) => keep(`<code>${esc(c)}</code>`));
    s = s.replace(/\[([^\]\n]+)\]\(([^()\s]+)\)/g, (all, text, url) => {
      const u = safeUrl(url);
      return u ? keep(link(u, inline(text, live))) : all;
    });
    s = s.replace(/\bhttps?:\/\/[^\s<>\u0000]+/gi, (url) => {
      if (live) return keep(esc(url));
      const tail = (/[.,;:!?)\]'"]+$/.exec(url) || [''])[0];
      const clean = url.slice(0, url.length - tail.length);
      return keep(link(clean, esc(clean))) + tail;
    });
    s = esc(s);
    s = s.replace(/\*\*([^\s*](?:.*?[^\s*])?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/(^|[^A-Za-z0-9_äöüÄÖÜß])__([^\s_](?:.*?[^\s_])?)__(?![A-Za-z0-9_äöüÄÖÜß])/g, '$1<strong>$2</strong>');
    s = s.replace(/(^|[^\wäöüÄÖÜß*])\*([^\s*](?:[^*\n]*[^\s*])?)\*(?![\wäöüÄÖÜß*])/g, '$1<em>$2</em>');
    s = s.replace(/(^|[^A-Za-z0-9_äöüÄÖÜß])_([^\s_](?:[^_\n]*[^\s_])?)_(?![A-Za-z0-9_äöüÄÖÜß])/g, '$1<em>$2</em>');
    s = s.replace(/~~([^\s~](?:.*?[^\s~])?)~~/g, '<del>$1</del>');
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => slots[Number(i)]);
  }

  // Listen als Baum: tiefer eingerueckt = Unterliste des vorigen Punkts
  function renderList(lines, from) {
    const top = { indent: -1, items: [] };
    const stack = [];
    let i = from;
    const open = (indent, ordered, start, parentItems) => {
      const level = { indent, ordered, start, items: [] };
      parentItems.push(level);
      stack.push(level);
      return level;
    };
    for (; i < lines.length; i++) {
      const line = lines[i];
      if (!line.trim()) {
        let n = i + 1;
        while (n < lines.length && !lines[n].trim()) n++;
        if (n < lines.length && LIST.test(lines[n])) {
          i = n - 1;
          continue;
        }
        break;
      }
      const m = LIST.exec(line);
      if (!m) {
        // eingerueckte Zeile ohne Zeichen: gehoert noch zum letzten Punkt
        const last = stack.length && stack[stack.length - 1].items.slice(-1)[0];
        if (last && /^[ \t]+\S/.test(line)) {
          last.text.push(line.trim());
          continue;
        }
        break;
      }
      const indent = indentOf(m[1]);
      const ordered = /\d/.test(m[2]);
      const start = ordered ? parseInt(m[2], 10) : 1;
      while (stack.length > 1 && stack[stack.length - 2].indent >= indent) stack.pop();
      let level = stack[stack.length - 1];
      if (!level) level = open(indent, ordered, start, top.items);
      else if (indent > level.indent) {
        const parent = level.items[level.items.length - 1];
        level = open(indent, ordered, start, parent.children);
      } else if (level.ordered !== ordered) {
        const holder = stack.length > 1 ? stack[stack.length - 2].items.slice(-1)[0].children : top.items;
        stack.pop();
        level = open(indent, ordered, start, holder);
      }
      level.items.push({ text: [m[5]], task: m[4] ? (/x/i.test(m[4]) ? 'done' : 'open') : null, children: [] });
    }
    const html = (level) => {
      const tag = level.ordered ? 'ol' : 'ul';
      const start = level.ordered && level.start !== 1 ? ` start="${level.start}"` : '';
      const items = level.items.map((it) => {
        const body = it.text.map(inline).join('<br>');
        const box = it.task ? `<input type="checkbox" disabled${it.task === 'done' ? ' checked' : ''}> ` : '';
        return `<li${it.task ? ' class="md-task"' : ''}>${box}${body}${it.children.map(html).join('')}</li>`;
      });
      return `<${tag}${start}>${items.join('')}</${tag}>`;
    };
    return { html: top.items.map(html).join(''), next: i };
  }

  function renderMarkdown(text) {
    const lines = String(text == null ? '' : text).replace(/\r\n?/g, '\n').split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        i++;
        continue;
      }
      const fence = /^\s*```/.test(line);
      if (fence) {
        const code = [];
        i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
        i++;
        out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
        continue;
      }
      const h = /^(#{1,3})[ \t]+(.+?)[ \t]*#*[ \t]*$/.exec(line);
      if (h) {
        out.push(`<div class="md-h md-h${h[1].length}">${inline(h[2])}</div>`);
        i++;
        continue;
      }
      if (/^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$/.test(line)) {
        out.push('<hr>');
        i++;
        continue;
      }
      if (LIST.test(line)) {
        const r = renderList(lines, i);
        out.push(r.html);
        i = r.next;
        continue;
      }
      if (/^[ \t]*>/.test(line)) {
        const quote = [];
        while (i < lines.length && /^[ \t]*>/.test(lines[i])) quote.push(lines[i++].replace(/^[ \t]*>[ \t]?/, ''));
        out.push(`<blockquote>${renderMarkdown(quote.join('\n'))}</blockquote>`);
        continue;
      }
      const para = [];
      while (
        i < lines.length &&
        lines[i].trim() &&
        !LIST.test(lines[i]) &&
        !/^(#{1,3})[ \t]+\S/.test(lines[i]) &&
        !/^[ \t]*>/.test(lines[i]) &&
        !/^\s*```/.test(lines[i]) &&
        !/^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$/.test(lines[i])
      ) {
        para.push(lines[i++].trim());
      }
      out.push(`<p>${para.map(inline).join('<br>')}</p>`);
    }
    return out.join('');
  }

  // Einzeiler ohne Markdown-Zeichen (Termin-Karten). Das Ergebnis ist reiner Text, noch nicht escaped.
  function markdownPlain(text) {
    const strip = (s) =>
      s
        .replace(/\[([^\]\n]+)\]\([^()\s]+\)/g, '$1')
        .replace(/`([^`\n]+)`/g, '$1')
        .replace(/\*\*([^\s*](?:.*?[^\s*])?)\*\*/g, '$1')
        .replace(/__([^\s_](?:.*?[^\s_])?)__/g, '$1')
        .replace(/~~([^\s~](?:.*?[^\s~])?)~~/g, '$1')
        .replace(/(^|[^\wäöüÄÖÜß*])\*([^\s*](?:[^*\n]*[^\s*])?)\*(?![\wäöüÄÖÜß*])/g, '$1$2');
    return String(text == null ? '' : text)
      .replace(/\r\n?/g, '\n')
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l && !/^```/.test(l) && !/^([-*_])([ \t]*\1){2,}$/.test(l))
      .map((l) => {
        const m = LIST.exec(l);
        const body = m ? m[5] : l.replace(/^#{1,3}[ \t]+/, '').replace(/^>[ \t]?/, '');
        return strip(body).trim();
      })
      .filter(Boolean)
      .join(' · ');
  }

  // ---- Bearbeiten: reine Funktionen (Text + Markierung rein, neuer Stand raus) ----
  function toLC(lines, pos) {
    let l = 0;
    while (l < lines.length - 1 && pos > lines[l].length) {
      pos -= lines[l].length + 1;
      l++;
    }
    return { l, c: Math.min(pos, lines[l].length) };
  }
  function fromLC(lines, p) {
    let pos = p.c;
    for (let i = 0; i < p.l; i++) pos += lines[i].length + 1;
    return pos;
  }
  function markerEnd(m) {
    return m[1].length + m[2].length + m[3].length + (m[4] ? m[4].length : 0);
  }

  // Nummerierte Listen fortlaufend halten; die erste Zahl einer Liste bleibt, wie sie ist
  function renumber(lines, sel) {
    const count = new Map();
    lines.forEach((line, l) => {
      if (!line.trim()) return;
      const m = LIST.exec(line);
      if (!m) {
        if (!/^[ \t]/.test(line)) count.clear();
        return;
      }
      const ind = indentOf(m[1]);
      for (const k of Array.from(count.keys())) if (k > ind) count.delete(k);
      if (!/\d/.test(m[2])) {
        count.delete(ind);
        return;
      }
      const n = parseInt(m[2], 10);
      const want = count.has(ind) ? count.get(ind) + 1 : n;
      count.set(ind, want);
      if (want === n) return;
      const mark = want + m[2].slice(-1);
      const delta = mark.length - m[2].length;
      lines[l] = m[1] + mark + line.slice(m[1].length + m[2].length);
      for (const p of sel) if (p.l === l && p.c > m[1].length) p.c += delta;
    });
  }
  function done(lines, a, b) {
    renumber(lines, [a, b]);
    return { text: lines.join('\n'), start: fromLC(lines, a), end: fromLC(lines, b) };
  }
  function selectedLines(lines, a, b) {
    const last = b.l > a.l && b.c === 0 ? b.l - 1 : b.l;
    return { first: a.l, last };
  }

  // Zeilen ein- (dir 1) oder ausruecken (dir -1). Eingerueckte Zahl beginnt eine Unterliste bei 1.
  function mdIndent(text, start, end, dir) {
    const lines = text.split('\n');
    const a = toLC(lines, start);
    const b = toLC(lines, end);
    const { first, last } = selectedLines(lines, a, b);
    for (let l = first; l <= last; l++) {
      if (!lines[l].trim() && first !== last) continue;
      const lead = /^[ \t]*/.exec(lines[l])[0];
      const was = lead.length;
      const width = indentOf(lead);
      const next = dir > 0 ? width + INDENT.length : Math.max(0, width - INDENT.length);
      const oldLen = lines[l].length;
      let body = lines[l].slice(was);
      if (dir > 0) body = body.replace(/^\d{1,9}(?=[.)][ \t])/, '1');
      lines[l] = ' '.repeat(next) + body;
      const delta = lines[l].length - oldLen;
      for (const p of [a, b]) if (p.l === l) p.c = p.c >= was ? Math.max(next, p.c + delta) : Math.min(p.c, next);
    }
    return done(lines, a, b);
  }

  // Enter: Liste fortfuehren. Leerer Punkt: ausruecken bzw. Liste beenden. null = normaler Umbruch.
  function mdEnter(text, pos) {
    const lines = text.split('\n');
    const p = toLC(lines, pos);
    const line = lines[p.l];
    const m = LIST.exec(line);
    if (!m) {
      const lead = /^[ \t]+/.exec(line);
      if (!lead || p.c < lead[0].length) return null;
      lines[p.l] = line.slice(0, p.c);
      lines.splice(p.l + 1, 0, lead[0] + line.slice(p.c));
      const at = { l: p.l + 1, c: lead[0].length };
      return done(lines, at, { ...at });
    }
    const end = markerEnd(m);
    if (p.c < end) return null;
    if (!m[5].trim()) {
      if (indentOf(m[1]) > 0) return mdIndent(text, pos, pos, -1);
      lines[p.l] = '';
      const at = { l: p.l, c: 0 };
      return done(lines, at, { ...at });
    }
    const ordered = /\d/.test(m[2]);
    const mark = ordered ? parseInt(m[2], 10) + 1 + m[2].slice(-1) : m[2];
    const head = m[1] + mark + m[3] + (m[4] ? '[ ] ' : '');
    lines[p.l] = line.slice(0, p.c);
    lines.splice(p.l + 1, 0, head + line.slice(p.c));
    const at = { l: p.l + 1, c: head.length };
    return done(lines, at, { ...at });
  }

  // Ruecktaste direkt hinter dem Listenzeichen: ausruecken, sonst Zeichen entfernen. null = normal.
  function mdBackspace(text, pos) {
    const lines = text.split('\n');
    const p = toLC(lines, pos);
    const m = LIST.exec(lines[p.l]);
    if (!m || p.c !== markerEnd(m)) return null;
    if (indentOf(m[1]) > 0) return mdIndent(text, pos, pos, -1);
    lines[p.l] = lines[p.l].slice(p.c);
    const at = { l: p.l, c: 0 };
    return done(lines, at, { ...at });
  }

  // **fett** usw. um die Markierung setzen; steht es schon drum, wieder entfernen
  function mdWrap(text, start, end, mark) {
    const n = mark.length;
    if (text.slice(start - n, start) === mark && text.slice(end, end + n) === mark) {
      return { text: text.slice(0, start - n) + text.slice(start, end) + text.slice(end + n), start: start - n, end: end - n };
    }
    const sel = text.slice(start, end);
    if (sel.startsWith(mark) && sel.endsWith(mark) && sel.length >= n * 2) {
      const inner = sel.slice(n, sel.length - n);
      return { text: text.slice(0, start) + inner + text.slice(end), start, end: start + inner.length };
    }
    return { text: text.slice(0, start) + mark + sel + mark + text.slice(end), start: start + n, end: end + n };
  }

  // ---- Textfeld im Browser ----
  // Ein bearbeitbares Feld zeigt das Ergebnis, das (versteckte) Textfeld behaelt den Markdown-Text.
  // Quelle ist immer der Text: Eingabe -> Text lesen -> neu aufbauen, wenn Markdown entstanden ist.
  // Jede Zeile ist ein <div>; ihr Zeichen (data-pre) steht nur als Attribut im DOM, so bleibt der
  // Text beim Zurueckschreiben unveraendert.
  const CARET = ''; // Anfang der Markierung im Text
  const CARET_END = ''; // Ende der Markierung
  const CARET_ANY = /[]/g;
  const ZWSP = '​'; // haelt den Cursor hinter fettem Text, damit Weitertippen nicht fett wird
  const BULLETS = ['•', '◦', '▪'];

  // Text -> Zeilen. Die Zeile mit dem Cursor wird noch keine Trennlinie, solange man dort tippt.
  function liveLines(text, caretLine) {
    let fence = false;
    return text.split('\n').map((line, i) => {
      if (/^\s*```/.test(line)) {
        fence = !fence;
        return { cls: 'md-l md-l-raw', pre: '', body: line, raw: true };
      }
      if (fence) return { cls: 'md-l md-l-raw', pre: '', body: line, raw: true };
      if (i !== caretLine && /^[ \t]*([-*_])([ \t]*\1){2,}[ \t]*$/.test(line)) {
        return { cls: 'md-l md-l-hr', pre: line, body: '', hr: true };
      }
      const h = /^(#{1,3}[ \t]+)(.*)$/.exec(line);
      if (h) return { cls: `md-l md-h md-h${h[1].trim().length}`, pre: h[1], body: h[2] };
      const m = LIST.exec(line);
      if (m) {
        const ind = Math.floor(indentOf(m[1]) / 2);
        const task = m[4] ? (/x/i.test(m[4]) ? 'done' : 'open') : null;
        const mark = task ? (task === 'done' ? '☑' : '☐') : /\d/.test(m[2]) ? m[2] : BULLETS[ind % 3];
        return { cls: 'md-l md-l-li', pre: m[1] + m[2] + m[3] + (m[4] || ''), body: m[5], ind, mark, task };
      }
      const q = /^([ \t]*>[ \t]?)(.*)$/.exec(line);
      if (q) return { cls: 'md-l md-l-quote', pre: q[1], body: q[2] };
      return { cls: 'md-l', pre: '', body: line };
    });
  }
  function liveHtml(L, body) {
    if (L.hr) return '<hr contenteditable="false">';
    return (L.raw ? esc(body) : inline(body, true)) || '<br>';
  }
  function makeLine(L) {
    const el = document.createElement('div');
    el.className = L.cls;
    el.dataset.pre = L.pre;
    if (L.mark) {
      el.dataset.mark = L.mark;
      el.style.setProperty('--ind', L.ind);
    }
    if (L.task) el.dataset.task = L.task;
    return el;
  }
  function wrapMarks(inner, open, close) {
    // Leerzeichen und Cursor gehoeren vor/hinter die Zeichen, sonst gilt **a ** nicht als fett
    const m = /^([\s]*)([\s\S]*?)([\s]*)$/.exec(inner);
    return m[2] ? m[1] + open + m[2] + close + m[3] : inner;
  }
  // Inhalt einer Zeile als Markdown; marks = Stellen, an denen ein Cursor-Zeichen einzufuegen ist
  function bodyOf(node, marks) {
    const at = (n, o) => marks.filter((m) => m.node === n && m.offset === o).map((m) => m.ch).join('');
    if (node.nodeType === 3) {
      let out = '';
      for (let i = 0; i <= node.data.length; i++) out += at(node, i) + (i < node.data.length ? node.data[i] : '');
      return out.split(ZWSP).join('');
    }
    if (node.nodeType !== 1) return '';
    let inner = '';
    node.childNodes.forEach((c, i) => {
      inner += at(node, i) + bodyOf(c, marks);
    });
    inner += at(node, node.childNodes.length);
    const tag = node.tagName;
    if (tag === 'STRONG' || tag === 'B') return wrapMarks(inner, '**', '**');
    if (tag === 'EM' || tag === 'I') return wrapMarks(inner, '*', '*');
    if (tag === 'DEL' || tag === 'S') return wrapMarks(inner, '~~', '~~');
    if (tag === 'CODE') return wrapMarks(inner, '`', '`');
    if (tag === 'A') return `[${inner}](${node.getAttribute('href')})`;
    return inner;
  }
  function normLine(html) {
    const s = html.split(ZWSP).join('').replace(/<br>$/, '');
    return s || '<br>';
  }
  function normHtml(html) {
    const t = document.createElement('template');
    t.innerHTML = html;
    return normLine(t.innerHTML);
  }

  function enhanceMarkdownField(ta) {
    if (!ta || ta.dataset.mdReady) return;
    ta.dataset.mdReady = '1';
    const ed = document.createElement('div');
    ed.className = `${ta.className} md-live`;
    ed.contentEditable = 'true';
    ed.setAttribute('role', 'textbox');
    ed.setAttribute('aria-multiline', 'true');
    ed.setAttribute('aria-label', ta.placeholder || 'Beschreibung');
    ed.dataset.ph = ta.placeholder || '';
    ed.style.minHeight = `${ta.offsetHeight || 92}px`;
    if (ta.style.lineHeight) ed.style.lineHeight = ta.style.lineHeight;
    ta.parentNode.insertBefore(ed, ta);
    ta.style.display = 'none';

    const undo = [];
    let redo = [];
    let cur = { text: ta.value, start: ta.value.length, end: ta.value.length };
    let lastEdit = 0;

    function readState() {
      const sel = window.getSelection();
      const marks = [];
      const lines = Array.from(ed.children);
      if (sel && sel.rangeCount && ed.contains(sel.anchorNode)) {
        const r = sel.getRangeAt(0);
        const spot = (node, offset, ch) => {
          if (node !== ed) return marks.push({ node, offset, ch });
          const last = lines[lines.length - 1];
          if (!last) return null;
          if (offset < lines.length) return marks.push({ node: lines[offset], offset: 0, ch });
          return marks.push({ node: last, offset: last.childNodes.length, ch });
        };
        spot(r.startContainer, r.startOffset, CARET);
        if (!r.collapsed) spot(r.endContainer, r.endOffset, CARET_END);
      }
      const raw = lines.map((el) => (el.dataset.pre || '') + bodyOf(el, marks)).join('\n');
      const start = raw.indexOf(CARET);
      const endRaw = raw.indexOf(CARET_END);
      const text = raw.replace(CARET_ANY, '');
      if (start < 0) return { text, start: text.length, end: text.length };
      return { text, start, end: endRaw < 0 ? start : endRaw - 1 };
    }
    function lineOf(text, pos) {
      return text.slice(0, pos).split('\n').length - 1;
    }
    function lineStart(text, pos) {
      return text.slice(0, pos).lastIndexOf('\n') + 1;
    }
    function sameAsDom(lines) {
      const els = ed.children;
      if (els.length !== lines.length) return false;
      return lines.every((L, i) => {
        const el = els[i];
        return el.className === L.cls && el.dataset.pre === L.pre && normLine(el.innerHTML) === normHtml(liveHtml(L, L.body));
      });
    }
    function placeMarks() {
      const walker = document.createTreeWalker(ed, NodeFilter.SHOW_TEXT);
      const found = {};
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const n of nodes) {
        for (const ch of [CARET, CARET_END]) {
          const i = n.data.indexOf(ch);
          if (i < 0) continue;
          n.deleteData(i, 1);
          found[ch] = { node: n, offset: i };
        }
      }
      return found;
    }
    function render(st, focus) {
      const caretLine = lineOf(st.text, st.start);
      const endLine = lineOf(st.text, st.end);
      const lines = liveLines(st.text, caretLine);
      const collapsed = st.start === st.end;
      let lineStart = 0;
      const frag = document.createDocumentFragment();
      let fallback = null;
      lines.forEach((L, i) => {
        const full = L.pre + L.body;
        const el = makeLine(L);
        const clean = liveHtml(L, L.body);
        const cuts = [];
        if (i === caretLine && !L.hr) cuts.push([Math.max(0, st.start - lineStart - L.pre.length), CARET]);
        if (!collapsed && i === endLine && !L.hr) cuts.push([Math.max(0, st.end - lineStart - L.pre.length), CARET_END]);
        let body = L.body;
        for (const [col, ch] of cuts.slice().reverse()) body = body.slice(0, col) + ch + body.slice(col);
        let html = clean;
        if (cuts.length && clean !== '<br>') {
          html = liveHtml(L, body);
          // Cursor-Zeichen darf das Ergebnis nicht aendern (z. B. *|*) und nicht in einer Adresse stecken
          if (html.replace(CARET_ANY, '') !== clean || /<[^>]*[][^>]*>/.test(html)) {
            html = clean;
            fallback = el;
          }
        } else if (cuts.length) fallback = el;
        el.innerHTML = html;
        frag.appendChild(el);
        lineStart += full.length + 1;
      });
      ed.textContent = '';
      ed.appendChild(frag);
      const found = placeMarks();
      if (focus === false) return;
      const sel = window.getSelection();
      const r = document.createRange();
      const a = found[CARET];
      if (a) {
        const prev = a.node.previousSibling;
        // hinter fettem Text oder in einem leeren Stueck: Platz schaffen, sonst springt der Cursor hinein
        if (collapsed && a.offset === 0 && (!a.node.data || (prev && prev.nodeType === 1))) {
          a.node.insertData(0, ZWSP);
          a.offset = 1;
        }
        r.setStart(a.node, a.offset);
      } else if (fallback) {
        r.selectNodeContents(fallback);
        r.collapse(false);
      } else {
        r.selectNodeContents(ed);
        r.collapse(false);
      }
      if (found[CARET_END]) r.setEnd(found[CARET_END].node, found[CARET_END].offset);
      else r.collapse(true);
      sel.removeAllRanges();
      sel.addRange(r);
      const line = ed.children[caretLine];
      if (line && line.scrollIntoView) line.scrollIntoView({ block: 'nearest' });
    }
    function emptyState() {
      ed.classList.toggle('is-empty', !cur.text);
    }
    function publish(st) {
      cur = st;
      ta.value = st.text;
      emptyState();
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
    function remember(merge) {
      const now = Date.now();
      if (!merge || now - lastEdit > 700 || !undo.length) undo.push(cur);
      if (undo.length > 100) undo.shift();
      redo = [];
      lastEdit = now;
    }
    // Aenderung am Text: in die Historie, neu aufbauen, Cursor setzen
    function apply(st) {
      if (!st) return;
      remember(false);
      render(st);
      publish(st);
    }
    // Nach normalem Tippen: Text lesen, nur neu aufbauen, wenn Markdown entstanden ist
    function sync() {
      const st = readState();
      if (st.text !== cur.text) remember(true);
      if (!sameAsDom(liveLines(st.text, lineOf(st.text, st.start)))) render(st);
      if (st.text !== cur.text) publish(st);
      else cur = st;
    }
    function replaceSel(st, insert) {
      let start = st.start;
      // Markierung ueber mehrere Zeilen ab Textanfang: das Listenzeichen der ersten Zeile geht mit
      if (st.text.slice(start, st.end).includes('\n')) {
        const from = lineStart(st.text, start);
        const i = lineOf(st.text, start);
        if (start === from + liveLines(st.text, -1)[i].pre.length) start = from;
      }
      const pos = start + insert.length;
      return { text: st.text.slice(0, start) + insert + st.text.slice(st.end), start: pos, end: pos };
    }
    function onEnter() {
      let st = readState();
      if (st.start !== st.end) st = replaceSel(st, '');
      const lines = st.text.split('\n');
      const line = lines[lineOf(st.text, st.start)];
      const quote = /^[ \t]*>[ \t]?/.exec(line);
      if (quote) {
        // Zitat fortfuehren; leere Zitat-Zeile beendet es
        if (line === quote[0]) {
          const from = st.start - line.length;
          return apply({ text: st.text.slice(0, from) + st.text.slice(st.start), start: from, end: from });
        }
        return apply(replaceSel(st, `\n${quote[0]}`));
      }
      apply(mdEnter(st.text, st.start) || replaceSel(st, '\n'));
    }
    // Ruecktaste am Zeilenanfang; false = der Browser loescht normal
    function onBackspace() {
      const st = readState();
      if (st.start !== st.end) {
        if (st.text.slice(st.start, st.end).includes('\n')) {
          apply(replaceSel(st, ''));
          return true;
        }
        return false;
      }
      const lines = st.text.split('\n');
      const i = lineOf(st.text, st.start);
      const from = lineStart(st.text, st.start);
      const L = liveLines(st.text, i)[i];
      if (st.start !== from + L.pre.length) return afterMarks(st, from);
      if (L.pre) {
        const r = mdBackspace(st.text, st.start);
        apply(r || { text: st.text.slice(0, from) + st.text.slice(from + L.pre.length), start: from, end: from });
        return true;
      }
      if (i === 0) return true;
      const prev = liveLines(st.text, -1)[i - 1];
      if (prev.hr) {
        const pl = lines[i - 1].length + 1;
        apply({ text: st.text.slice(0, from - pl) + st.text.slice(from), start: from - pl, end: from - pl });
        return true;
      }
      apply({ text: st.text.slice(0, from - 1) + st.text.slice(from), start: from - 1, end: from - 1 });
      return true;
    }
    // Cursor direkt hinter fettem Text: letztes Zeichen darin loeschen. Der Browser wuerde sonst
    // nur den unsichtbaren Platzhalter treffen und die Taste scheinbar nichts tun.
    function afterMarks(st, from) {
      const sel = window.getSelection();
      const r = sel.getRangeAt(0).cloneRange();
      r.setStart(ed.children[lineOf(st.text, st.start)], 0);
      if (!r.toString().endsWith(ZWSP)) return false;
      const m = /(\*\*|\*|~~|`)$/.exec(st.text.slice(from, st.start));
      if (!m) return false;
      const n = m[1].length;
      const close = st.start - n;
      let text = st.text.slice(0, close - 1) + st.text.slice(close);
      let pos = st.start - 1;
      // war es das letzte Zeichen darin, gehen auch die Zeichen drumherum
      if (st.text.slice(close - 1 - n, close - 1) === m[1]) {
        text = st.text.slice(0, close - 1 - n) + st.text.slice(st.start);
        pos = close - 1 - n;
      }
      apply({ text, start: pos, end: pos });
      return true;
    }
    // Entf am Zeilenende holt die naechste Zeile ohne ihr Listenzeichen hoch
    function onDelete() {
      const st = readState();
      if (st.start !== st.end) {
        if (st.text.slice(st.start, st.end).includes('\n')) {
          apply(replaceSel(st, ''));
          return true;
        }
        return false;
      }
      if (st.text[st.start] !== '\n') return false;
      const i = lineOf(st.text, st.start);
      const next = liveLines(st.text, -1)[i + 1];
      const cut = st.start + 1 + next.pre.length;
      apply({ text: st.text.slice(0, st.start) + st.text.slice(cut), start: st.start, end: st.start });
      return true;
    }
    function format(mark) {
      const st = readState();
      apply(mdWrap(st.text, st.start, st.end, mark));
    }
    function stepHistory(from, to) {
      const st = from.pop();
      if (!st) return;
      to.push(cur);
      render(st);
      cur = st;
      ta.value = st.text;
      emptyState();
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }

    // beforeinput statt keydown: meldet Enter und Ruecktaste auch mit Bildschirmtastaturen
    ed.addEventListener('beforeinput', (ev) => {
      const t = ev.inputType;
      if (t === 'historyUndo' || t === 'historyRedo') {
        ev.preventDefault();
        if (t === 'historyUndo') stepHistory(undo, redo);
        else stepHistory(redo, undo);
        return;
      }
      if (t === 'formatBold' || t === 'formatItalic') {
        ev.preventDefault();
        format(t === 'formatBold' ? '**' : '*');
        return;
      }
      if (t === 'formatUnderline' || t === 'formatStrikeThrough') {
        ev.preventDefault();
        return;
      }
      if (ev.isComposing) return;
      if (t === 'insertLineBreak' || t === 'insertParagraph') {
        ev.preventDefault();
        onEnter();
      } else if (t === 'deleteContentBackward' || t === 'deleteWordBackward' || t === 'deleteSoftLineBackward' || t === 'deleteHardLineBackward') {
        if (onBackspace()) ev.preventDefault();
      } else if (t === 'deleteContentForward' || t === 'deleteWordForward') {
        if (onDelete()) ev.preventDefault();
      } else if (t === 'deleteByCut' || t === 'deleteContent') {
        const st = readState();
        if (st.text.slice(st.start, st.end).includes('\n')) {
          ev.preventDefault();
          apply(replaceSel(st, ''));
        }
      } else if (t === 'insertText' && ev.data) {
        const st = readState();
        if (st.text.slice(st.start, st.end).includes('\n')) {
          ev.preventDefault();
          apply(replaceSel(st, ev.data));
        }
      }
    });
    ed.addEventListener('input', (ev) => {
      if (!ev.isComposing) sync();
    });
    ed.addEventListener('compositionend', () => setTimeout(sync, 0));
    ed.addEventListener('paste', (ev) => {
      ev.preventDefault();
      const text = (ev.clipboardData && ev.clipboardData.getData('text/plain')) || '';
      if (text) apply(replaceSel(readState(), text.replace(/\r\n?/g, '\n')));
    });
    // Beim Ausschneiden ueber mehrere Zeilen nur reinen Text in die Zwischenablage
    ed.addEventListener('copy', copyPlain);
    ed.addEventListener('cut', copyPlain);
    function copyPlain(ev) {
      const st = readState();
      if (st.start === st.end || !ev.clipboardData) return;
      ev.clipboardData.setData('text/plain', st.text.slice(st.start, st.end));
      ev.preventDefault();
      if (ev.type === 'cut') apply(replaceSel(st, ''));
    }
    ed.addEventListener('drop', (ev) => ev.preventDefault());
    ed.addEventListener('keydown', (ev) => {
      const meta = ev.ctrlKey || ev.metaKey;
      if (meta && !ev.altKey && /^[bi]$/i.test(ev.key)) {
        ev.preventDefault();
        format(ev.key.toLowerCase() === 'b' ? '**' : '*');
        return;
      }
      if (meta && !ev.altKey && (/^z$/i.test(ev.key) || /^y$/i.test(ev.key))) {
        ev.preventDefault();
        if (/^z$/i.test(ev.key) && !ev.shiftKey) stepHistory(undo, redo);
        else stepHistory(redo, undo);
        return;
      }
      // Safari meldet am Textanfang kein beforeinput - das Zeichen der ersten Zeile muss trotzdem weg
      if (ev.key === 'Backspace' && !ev.isComposing) {
        if (onBackspace()) ev.preventDefault();
        return;
      }
      if (ev.key !== 'Tab' || meta || ev.altKey) return;
      const st = readState();
      const lines = st.text.split('\n');
      const line = lines[lineOf(st.text, st.start)];
      const multi = st.text.slice(st.start, st.end).includes('\n');
      // Tab springt sonst weiter zum naechsten Feld - nur in Listen oder bei mehreren Zeilen einruecken
      if (!LIST.test(line) && !multi && !(ev.shiftKey && /^[ \t]/.test(line))) return;
      ev.preventDefault();
      apply(mdIndent(st.text, st.start, st.end, ev.shiftKey ? -1 : 1));
    });
    // Checkbox: Tipp auf das Kaestchen hakt ab
    ed.addEventListener('pointerdown', (ev) => {
      const el = ev.target.closest && ev.target.closest('.md-l[data-task]');
      if (!el || ev.target !== el) return;
      const left = el.getBoundingClientRect().left + parseFloat(getComputedStyle(el).paddingLeft);
      if (ev.clientX >= left) return;
      ev.preventDefault();
      const i = Array.prototype.indexOf.call(ed.children, el);
      const lines = cur.text.split('\n');
      lines[i] = lines[i].replace(/\[( |x|X)\]/, (_, c) => (c === ' ' ? '[x]' : '[ ]'));
      const text = lines.join('\n');
      const pos = lines.slice(0, i + 1).join('\n').length;
      remember(false);
      render({ text, start: pos, end: pos }, false);
      publish({ text, start: pos, end: pos });
    });
    // Verlaesst man eine Zeile mit ---, wird sie jetzt zur Trennlinie
    ed.addEventListener('blur', () => {
      if (!sameAsDom(liveLines(cur.text, -1))) render({ text: cur.text, start: -1, end: -1 }, false);
    });

    render(cur, false);
    emptyState();
  }
  function enhanceMarkdownFields(scope) {
    (scope || document).querySelectorAll('textarea[data-markdown]').forEach(enhanceMarkdownField);
  }

  const api = { renderMarkdown, markdownPlain, enhanceMarkdownFields, mdEnter, mdBackspace, mdIndent, mdWrap };
  Object.assign(root, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
