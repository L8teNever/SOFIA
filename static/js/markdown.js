// Markdown fuer Beschreibungen: sichere Anzeige (renderMarkdown), Einzeiler fuer Karten
// (markdownPlain) und ein Textfeld, das Listen fortfuehrt und einrueckt (data-markdown).
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
  function inline(src) {
    const slots = [];
    const keep = (html) => {
      slots.push(html);
      return `\u0000${slots.length - 1}\u0000`;
    };
    let s = String(src).replace(/\u0000/g, '');
    s = s.replace(/\\([\\`*_~[\]()#+\-.!>])/g, (_, c) => keep(esc(c)));
    s = s.replace(/`([^`\n]+)`/g, (_, c) => keep(`<code>${esc(c)}</code>`));
    s = s.replace(/\[([^\]\n]+)\]\(([^()\s]+)\)/g, (all, text, url) => {
      const u = safeUrl(url);
      return u ? keep(link(u, inline(text))) : all;
    });
    s = s.replace(/\bhttps?:\/\/[^\s<>\u0000]+/gi, (url) => {
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
  // Nur den geaenderten Teil ersetzen und ueber execCommand gehen: so bleibt Strg+Z intakt
  function applyEdit(ta, r) {
    const old = ta.value;
    let p = 0;
    const max = Math.min(old.length, r.text.length);
    while (p < max && old[p] === r.text[p]) p++;
    let s = 0;
    while (s < max - p && old[old.length - 1 - s] === r.text[r.text.length - 1 - s]) s++;
    const insert = r.text.slice(p, r.text.length - s);
    ta.focus();
    ta.setSelectionRange(p, old.length - s);
    let ok = false;
    try {
      ok = insert ? document.execCommand('insertText', false, insert) : p === old.length - s || document.execCommand('delete');
    } catch (err) {}
    if (!ok || ta.value !== r.text) ta.value = r.text;
    ta.setSelectionRange(r.start, r.end);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }
  function fitHeight(ta) {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight + 2, Math.round(window.innerHeight * 0.45)) + 'px';
  }
  // Das Textfeld bleibt optisch unveraendert: keine Leiste, keine Hinweise - nur Tastatur
  function enhanceMarkdownField(ta) {
    if (!ta || ta.dataset.mdReady) return;
    ta.dataset.mdReady = '1';
    // beforeinput statt keydown: meldet Enter und Ruecktaste auch mit Bildschirmtastaturen
    ta.addEventListener('beforeinput', (ev) => {
      if (ev.isComposing || ta.selectionStart !== ta.selectionEnd) return;
      let r = null;
      if (ev.inputType === 'insertLineBreak' || ev.inputType === 'insertParagraph') r = mdEnter(ta.value, ta.selectionStart);
      else if (ev.inputType === 'deleteContentBackward') r = mdBackspace(ta.value, ta.selectionStart);
      if (!r) return;
      ev.preventDefault();
      applyEdit(ta, r);
    });
    ta.addEventListener('keydown', (ev) => {
      const meta = ev.ctrlKey || ev.metaKey;
      if (meta && !ev.altKey && /^[bi]$/i.test(ev.key)) {
        ev.preventDefault();
        applyEdit(ta, mdWrap(ta.value, ta.selectionStart, ta.selectionEnd, ev.key.toLowerCase() === 'b' ? '**' : '*'));
        return;
      }
      if (ev.key !== 'Tab' || meta || ev.altKey) return;
      const v = ta.value;
      const s = ta.selectionStart;
      const e = ta.selectionEnd;
      const lines = v.split('\n');
      const line = lines[toLC(lines, s).l];
      const multi = v.slice(s, e).includes('\n');
      // Tab springt sonst weiter zum naechsten Feld - nur in Listen oder bei mehreren Zeilen einruecken
      if (!LIST.test(line) && !multi && !(ev.shiftKey && /^[ \t]/.test(line))) return;
      ev.preventDefault();
      applyEdit(ta, mdIndent(v, s, e, ev.shiftKey ? -1 : 1));
    });
    ta.addEventListener('input', () => fitHeight(ta));
    requestAnimationFrame(() => fitHeight(ta));
  }
  function enhanceMarkdownFields(scope) {
    (scope || document).querySelectorAll('textarea[data-markdown]').forEach(enhanceMarkdownField);
  }

  const api = { renderMarkdown, markdownPlain, enhanceMarkdownFields, mdEnter, mdBackspace, mdIndent, mdWrap };
  Object.assign(root, api);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
