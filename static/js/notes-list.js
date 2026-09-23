
document.getElementById('ph-notes').outerHTML = pageHeader({ title: 'Tafel' });
document.getElementById('notes-fab-slot').outerHTML = fab({ onclick: 'openNewNotesBoardSheet()', title: 'Neues Board erstellen' });

const NOTES_COLORS = ['#eaddff','#d3e3fd','#c4eed0','#ffdec1','#ffd8e4','#fff3c4'];
let notesBoards = [];
let notesSubjects = [];

async function init_notes() {
  [notesBoards, notesSubjects] = await Promise.all([
    API.notesBoards('mine').catch(() => []),
    API.subjects().catch(() => []),
  ]);
  renderNotesList();

  // Deep-link from Drive (openNotesBoardFromDrive) or a page reload on
  // /notes/{id} — open straight into that board's canvas instead of the list.
  if (window.__notesOpenBoardId) {
    const id = window.__notesOpenBoardId;
    window.__notesOpenBoardId = null;
    window.openNotesBoardCanvas(id);
    return;
  }
  const pathMatch = location.pathname.match(/^\/notes\/(\d+)/);
  if (pathMatch) window.openNotesBoardCanvas(parseInt(pathMatch[1]));
}

function renderNotesList() {
  const list = document.getElementById('notes-list');
  const empty = document.getElementById('notes-empty');
  if (!notesBoards.length) {
    list.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  list.innerHTML = notesBoards.map(b => {
    const color = NOTES_COLORS[Math.abs(b.id) % NOTES_COLORS.length];
    const folderLabel = b.subject_name ? (b.topic ? `${b.subject_name} · ${b.topic}` : b.subject_name) : 'Kein Fach';
    return `
    <div class="notes-row" onclick="openNotesBoardCanvas(${b.id})">
      <div class="notes-icon" style="background:${color};">
        <i data-lucide="layout-dashboard" style="width:20px;height:20px;"></i>
      </div>
      <div style="flex:1;min-width:0;">
        <div style="font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(b.title)}</div>
        <div style="font-size:0.75rem;opacity:0.6;">${escapeHtml(folderLabel)}</div>
      </div>
      <span class="notes-vis-badge ${b.is_public ? 'public' : 'private'}">${b.is_public ? 'Öffentlich' : 'Privat'}</span>
      <button class="notes-row-action" onclick="event.stopPropagation();openEditNotesBoardSheet(${b.id})" title="Bearbeiten">
        <i data-lucide="pencil" style="width:16px;height:16px;"></i>
      </button>
      <button class="notes-row-action" onclick="event.stopPropagation();deleteNotesBoardConfirm(${b.id})" title="Löschen">
        <i data-lucide="trash-2" style="width:16px;height:16px;"></i>
      </button>
    </div>`;
  }).join('');
  if (window.lucide) lucide.createIcons();
}

let notesActiveEngine = null;

async function openNotesBoardCanvasImpl(id) {
  let board;
  try {
    board = await API.notesBoard(id);
  } catch {
    showToast('Board nicht gefunden');
    return;
  }
  window.__notesActiveBoard = board;
  const fn = document.getElementById('canvas-filename');
  if (fn) fn.value = board.title;
  document.getElementById('notes-canvas-view').style.display = 'block';
  if (window.lucide) lucide.createIcons();
  notesActiveEngine = createWhiteboardEngine(id);
}

function closeNotesCanvasView() {
  if (notesActiveEngine) { notesActiveEngine.stop(); notesActiveEngine = null; }
  document.getElementById('notes-canvas-view').style.display = 'none';
  init_notes(); // refresh the list (title/visibility may have changed elsewhere)
}

function notesSubjectOptions(selectedId) {
  return (notesSubjects || []).map(s =>
    `<option value="${s.id}" ${s.id === selectedId ? 'selected' : ''}>${escapeHtml(s.name || s.short_name || '')}</option>`
  ).join('');
}

function openNewNotesBoardSheet() {
  openSheet(`
    <div class="sheet-title" style="font-size:1.25rem;font-weight:800;color:#1c1b1f;margin-bottom:16px;">Neues Board</div>

    <div class="m3-field">
      <label class="m3-field-label">Titel:</label>
      <input class="m3-input" id="nb-title" placeholder="z. B. Mathe Mitschrift">
    </div>

    <div class="m3-field">
      <label class="m3-field-label">Fach (optional):</label>
      <select class="m3-select" id="nb-subject">
        <option value="">Kein Fach</option>
        ${notesSubjectOptions(null)}
      </select>
    </div>

    <div class="m3-field">
      <label class="m3-field-label">Ordner/Thema (optional):</label>
      <input class="m3-input" id="nb-topic" placeholder="z. B. Kapitel 3">
    </div>

    <div class="m3-field" style="display:flex;align-items:center;gap:10px;">
      <input type="checkbox" id="nb-public" style="width:18px;height:18px;accent-color:#6750a4;cursor:pointer;">
      <label for="nb-public" style="font-size:0.9rem;cursor:pointer;">Für die ganze Klasse sichtbar (auch in Drive)</label>
    </div>

    <button class="m3-btn-full" onclick="submitNewNotesBoard()">Board erstellen</button>
  `);
}

async function submitNewNotesBoard() {
  const title = document.getElementById('nb-title').value.trim();
  if (!title) { showToast('Bitte einen Titel eingeben'); return; }
  const subjectId = document.getElementById('nb-subject').value;
  const topic = document.getElementById('nb-topic').value.trim();
  const isPublic = document.getElementById('nb-public').checked;
  try {
    const board = await API.createNotesBoard({
      title,
      subject_id: subjectId ? parseInt(subjectId) : null,
      topic: topic || null,
      is_public: isPublic,
    });
    notesBoards.unshift(board);
    renderNotesList();
    closeSheet();
    showToast('Board erstellt');
  } catch {
    showToast('Fehler beim Erstellen');
  }
}

function openEditNotesBoardSheet(id) {
  const b = notesBoards.find(x => x.id === id);
  if (!b) return;
  openSheet(`
    <div class="sheet-title" style="font-size:1.25rem;font-weight:800;color:#1c1b1f;margin-bottom:16px;">Board bearbeiten</div>

    <div class="m3-field">
      <label class="m3-field-label">Titel:</label>
      <input class="m3-input" id="nb-edit-title" value="${escapeHtml(b.title)}">
    </div>

    <div class="m3-field">
      <label class="m3-field-label">Fach (optional):</label>
      <select class="m3-select" id="nb-edit-subject">
        <option value="">Kein Fach</option>
        ${notesSubjectOptions(b.subject_id)}
      </select>
    </div>

    <div class="m3-field">
      <label class="m3-field-label">Ordner/Thema (optional):</label>
      <input class="m3-input" id="nb-edit-topic" value="${escapeHtml(b.topic || '')}">
    </div>

    <div class="m3-field" style="display:flex;align-items:center;gap:10px;">
      <input type="checkbox" id="nb-edit-public" ${b.is_public ? 'checked' : ''} style="width:18px;height:18px;accent-color:#6750a4;cursor:pointer;">
      <label for="nb-edit-public" style="font-size:0.9rem;cursor:pointer;">Für die ganze Klasse sichtbar (auch in Drive)</label>
    </div>

    <button class="m3-btn-full" onclick="submitEditNotesBoard(${id})">Speichern</button>
  `);
}

async function submitEditNotesBoard(id) {
  const title = document.getElementById('nb-edit-title').value.trim();
  const subjectId = document.getElementById('nb-edit-subject').value;
  const topic = document.getElementById('nb-edit-topic').value.trim();
  const isPublic = document.getElementById('nb-edit-public').checked;
  try {
    const updated = await API.updateNotesBoard(id, {
      title: title || undefined,
      subject_id: subjectId ? parseInt(subjectId) : null,
      topic: topic || null,
      is_public: isPublic,
    });
    const idx = notesBoards.findIndex(x => x.id === id);
    if (idx !== -1) notesBoards[idx] = updated;
    renderNotesList();
    closeSheet();
    showToast('Gespeichert');
  } catch {
    showToast('Fehler beim Speichern');
  }
}

function deleteNotesBoardConfirm(id) {
  const b = notesBoards.find(x => x.id === id);
  if (!b) return;
  openModal(confirmDelete({
    title: 'Board löschen?',
    message: `<b>${escapeHtml(b.title)}</b> wird unwiderruflich gelöscht.`,
    onConfirm: `deleteNotesBoardDo(${id})`,
  }));
}

async function deleteNotesBoardDo(id) {
  try {
    await API.deleteNotesBoard(id);
    notesBoards = notesBoards.filter(x => x.id !== id);
    renderNotesList();
    closeModal();
    showToast('Gelöscht');
  } catch {
    showToast('Fehler beim Löschen');
  }
}

// ============================================================================
// Whiteboard drawing engine — ported from sofianotes' frontend/app.js almost
// verbatim (same variable names, same gesture/shape/undo logic), wrapped in
// a factory so a fresh, isolated instance is created per opened board
// instead of sofianotes' original top-level IIFE (which assumed one full
// standalone page load — this SPA tears down/rebuilds page DOM on every
// navigation, so a fixed-at-load IIFE would leak WS connections and RAF
// loops across repeated open/close). The WS message protocol is completely
// unchanged; only the WS URL (board-scoped) and this factory/teardown
// wrapper are new. Canvas-internal drawing/gesture math is untouched.
// ============================================================================

window.__init_notes_impl = init_notes;
window.__openNotesBoardCanvasImpl = openNotesBoardCanvasImpl;
