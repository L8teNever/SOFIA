// Ausfuehren: node tests/markdown.test.js
const assert = require('assert');
const { renderMarkdown: r, markdownPlain: plain, mdEnter, mdBackspace, mdIndent, mdWrap } = require('../static/js/markdown.js');

let n = 0;
const test = (name, fn) => {
  try {
    fn();
    n++;
  } catch (err) {
    console.error('FEHLER: ' + name);
    throw err;
  }
};
// "|" markiert den Cursor; zwei "|" eine Markierung
const parse = (s) => {
  const start = s.indexOf('|');
  const rest = s.slice(0, start) + s.slice(start + 1);
  const second = rest.indexOf('|');
  return second < 0 ? { text: rest, start, end: start } : { text: rest.slice(0, second) + rest.slice(second + 1), start, end: second };
};
const show = (x) => (x.start === x.end ? x.text.slice(0, x.start) + '|' + x.text.slice(x.start) : x.text.slice(0, x.start) + '|' + x.text.slice(x.start, x.end) + '|' + x.text.slice(x.end));
const enter = (s) => {
  const p = parse(s);
  const x = mdEnter(p.text, p.start);
  return x && show(x);
};
const back = (s) => {
  const p = parse(s);
  const x = mdBackspace(p.text, p.start);
  return x && show(x);
};
const ind = (s, d) => {
  const p = parse(s);
  return show(mdIndent(p.text, p.start, p.end, d));
};
const wrap = (s, m) => {
  const p = parse(s);
  return show(mdWrap(p.text, p.start, p.end, m));
};

test('Text und Zeilen', () => {
  assert.equal(r('Hallo\nWelt'), '<p>Hallo<br>Welt</p>');
  assert.equal(r('a\n\nb'), '<p>a</p><p>b</p>');
  assert.equal(r(''), '');
  assert.equal(r(null), '');
});
test('Formatierung', () => {
  assert.equal(r('**fett** und *kursiv* und ~~weg~~'), '<p><strong>fett</strong> und <em>kursiv</em> und <del>weg</del></p>');
  assert.equal(r('__fett__ _kursiv_'), '<p><strong>fett</strong> <em>kursiv</em></p>');
  assert.equal(r('`**nicht fett**`'), '<p><code>**nicht fett**</code></p>');
  assert.equal(r('\\*\\*kein fett\\*\\*'), '<p>**kein fett**</p>');
});
test('Alltagstext bleibt Text', () => {
  assert.equal(r('2 * 3 * 4'), '<p>2 * 3 * 4</p>');
  assert.equal(r('snake_case_name'), '<p>snake_case_name</p>');
  assert.equal(r('#hashtag'), '<p>#hashtag</p>');
  assert.equal(r('- '), '<ul><li></li></ul>');
  assert.equal(r('12:30 - 13:00 Pause'), '<p>12:30 - 13:00 Pause</p>');
});
test('Listen verschachtelt', () => {
  assert.equal(r('- a\n- b'), '<ul><li>a</li><li>b</li></ul>');
  assert.equal(r('- a\n  - b\n    - c\n- d'), '<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li><li>d</li></ul>');
  assert.equal(r('1. a\n2. b\n   - c'), '<ol><li>a</li><li>b<ul><li>c</li></ul></li></ol>');
  assert.equal(r('- a\n\t- b'), '<ul><li>a<ul><li>b</li></ul></li></ul>');
  assert.equal(r('- a\n    - b\n  - c'), '<ul><li>a<ul><li>b</li><li>c</li></ul></li></ul>');
  assert.equal(r('3. Stunde fällt aus'), '<ol start="3"><li>Stunde fällt aus</li></ol>');
  assert.equal(r('- a\n\n- b'), '<ul><li>a</li><li>b</li></ul>');
  assert.equal(r('- a\n  weiter\nText'), '<ul><li>a<br>weiter</li></ul><p>Text</p>');
  assert.equal(r('- a\n1. b'), '<ul><li>a</li></ul><ol><li>b</li></ol>');
  assert.equal(r('- a\n  - b\n  1. c'), '<ul><li>a<ul><li>b</li></ul><ol><li>c</li></ol></li></ul>');
});
test('Checklisten', () => {
  assert.equal(r('- [ ] offen\n- [x] fertig'), '<ul><li class="md-task"><input type="checkbox" disabled> offen</li><li class="md-task"><input type="checkbox" disabled checked> fertig</li></ul>');
});
test('Bloecke', () => {
  assert.equal(r('# Titel'), '<div class="md-h md-h1">Titel</div>');
  assert.equal(r('Text\n# Titel\nmehr'), '<p>Text</p><div class="md-h md-h1">Titel</div><p>mehr</p>');
  assert.equal(r('> zitat\n> zwei'), '<blockquote><p>zitat<br>zwei</p></blockquote>');
  assert.equal(r('---'), '<hr>');
  assert.equal(r('```\n<b>\n```'), '<pre><code>&lt;b&gt;</code></pre>');
});
test('Links', () => {
  const a = (u, t) => `<a href="${u}" target="_blank" rel="noopener noreferrer">${t}</a>`;
  assert.equal(r('[Seite](https://x.de/a)'), `<p>${a('https://x.de/a', 'Seite')}</p>`);
  assert.equal(r('siehe https://x.de/a, danke'), `<p>siehe ${a('https://x.de/a', 'https://x.de/a')}, danke</p>`);
  assert.equal(r('[**fett**](https://x.de)'), `<p>${a('https://x.de', '<strong>fett</strong>')}</p>`);
  assert.equal(r('https://x.de/a_b_c'), `<p>${a('https://x.de/a_b_c', 'https://x.de/a_b_c')}</p>`);
});
test('Kein Schadcode', () => {
  const bad = [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '[x](javascript:alert(1))',
    '[x](JaVaScRiPt:alert(1))',
    '[x](data:text/html,<script>alert(1)</script>)',
    '[x](https://a.de"onmouseover="alert(1))',
    'https://a.de/"><img src=x onerror=alert(1)>',
    '- <svg onload=alert(1)>',
    '# <b onclick=alert(1)>',
    '> <iframe src=x>',
    '`<script>`',
    '**<script>**',
    '\u00000\u0000<script>',
  ];
  for (const s of bad) {
    const h = r(s);
    assert(!/<(script|img|svg|iframe|b)\b/i.test(h), s + ' => ' + h);
    assert(!/href="(?!https?:|mailto:)/i.test(h), s + ' => ' + h);
    assert(!/<a [^>]*"\s*on\w+=/i.test(h), s + ' => ' + h);
  }
});
test('Einzeiler fuer Karten', () => {
  assert.equal(plain('# Titel\n- **a**\n  - [x] b\n\n[c](https://x.de)'), 'Titel · a · b · c');
  assert.equal(plain('2 * 3 * 4'), '2 * 3 * 4');
  assert.equal(plain('---'), '');
});
test('Enter fuehrt Listen fort', () => {
  assert.equal(enter('- a|'), '- a\n- |');
  assert.equal(enter('* a|'), '* a\n* |');
  assert.equal(enter('  - a|'), '  - a\n  - |');
  assert.equal(enter('1. a|'), '1. a\n2. |');
  assert.equal(enter('1) a|'), '1) a\n2) |');
  assert.equal(enter('- [x] a|'), '- [x] a\n- [ ] |');
  assert.equal(enter('- ab|cd'), '- ab\n- |cd');
  assert.equal(enter('Text|'), null);
  assert.equal(enter('|- a'), null);
});
test('Enter nummeriert den Rest neu', () => {
  assert.equal(enter('1. a|\n2. b\n3. c'), '1. a\n2. |\n3. b\n4. c');
  assert.equal(enter('9. a|'), '9. a\n10. |');
});
test('Enter auf leerem Punkt', () => {
  assert.equal(enter('- a\n- |'), '- a\n|');
  assert.equal(enter('- a\n  - |'), '- a\n- |');
  assert.equal(enter('- a\n  - [ ] |'), '- a\n- [ ] |');
});
test('Enter in eingerueckter Zeile behaelt den Einzug', () => {
  assert.equal(enter('  Text|'), '  Text\n  |');
});
test('Ruecktaste hinter dem Zeichen', () => {
  assert.equal(back('- |a'), '|a');
  assert.equal(back('  - |a'), '- |a');
  assert.equal(back('- a|'), null);
  assert.equal(back('Text|'), null);
  assert.equal(back('1. a\n2. |b\n3. c'), '1. a\n|b\n1. c'.replace('1. c', '3. c'));
});
test('Einruecken', () => {
  assert.equal(ind('- a\n- b|', 1), '- a\n  - b|');
  assert.equal(ind('- a\n  - b|', -1), '- a\n- b|');
  assert.equal(ind('- |a', 1), '  - |a');
  assert.equal(ind('|- a', 1), '  |- a');
  assert.equal(ind('a|', -1), 'a|');
  assert.equal(ind(' - a|', -1), '- a|');
  assert.equal(ind('\t- a|', -1), '  - a|');
  assert.equal(ind('|- a\n- b|', 1), '  |- a\n  - b|');
  assert.equal(ind('|- a\n- b\n|- c', 1), '  |- a\n  - b\n|- c');
});
test('Einruecken nummerierter Listen', () => {
  assert.equal(ind('1. a\n2. b|\n3. c', 1), '1. a\n  1. b|\n2. c');
  assert.equal(ind('1. a\n  1. b|\n2. c', -1), '1. a\n2. b|\n3. c');
  assert.equal(ind('1. a\n  1. b\n2. c|', 1), '1. a\n  1. b\n  2. c|');
});
test('Fett und kursiv', () => {
  assert.equal(wrap('|a|', '**'), '**|a|**');
  assert.equal(wrap('**|a|**', '**'), '|a|');
  assert.equal(wrap('|**a**|', '**'), '|a|');
  assert.equal(wrap('x|', '**'), 'x**|**');
});
console.log(n + ' Tests ok');
