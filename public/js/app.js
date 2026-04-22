import { api } from './api.js';
import { createEditor } from './editor.js';
import { createBrain } from './brain.js';
import { createColumns } from './columns.js';

const state = {
  view: 'brain',            // 'brain' | 'columns'
  currentId: null,          // current thought id (for brain view and editor)
  history: [],              // navigation stack of thought ids
};

const editor  = createEditor('#editor-host', '#save-status');
const brain   = createBrain('#cy',        { onSelect: selectThought });
const columns = createColumns('#columns', { onSelect: onColumnSelect });

async function selectThought(id, { pushHistory = true } = {}) {
  if (pushHistory && state.currentId != null && state.currentId !== id) {
    state.history.push(state.currentId);
  }
  state.currentId = id;
  await editor.load(id);
  await updateTitle();
  if (state.view === 'brain') await brain.render(id);
}

async function onColumnSelect(id) {
  state.currentId = id;
  await editor.load(id);
  await updateTitle();
}

async function updateTitle() {
  const el = document.getElementById('current-title');
  if (state.currentId == null) { el.textContent = ''; return; }
  const t = await api.getThought(state.currentId);
  el.textContent = t?.name ?? '';
}

function setView(view) {
  state.view = view;
  for (const btn of document.querySelectorAll('.view-btn')) {
    btn.classList.toggle('active', btn.dataset.view === view);
  }
  document.getElementById('brain-view').classList.toggle('active', view === 'brain');
  document.getElementById('columns-view').classList.toggle('active', view === 'columns');

  if (view === 'brain') {
    if (state.currentId != null) brain.render(state.currentId);
    setTimeout(() => brain.resize(), 0);
  } else {
    columns.reset();
  }
}

async function addLinked(kind) {
  if (state.currentId == null) return;
  const name = prompt(`Name of new ${kind === 'child' ? 'child' : kind === 'jump' ? 'jump' : 'parent'} thought (or existing thought name):`);
  if (!name || !name.trim()) return;
  const trimmed = name.trim();

  // Prefer an existing thought with exact name match, otherwise create new.
  const hits = await api.search(trimmed);
  const exact = hits.find(h => h.name.toLowerCase() === trimmed.toLowerCase());
  const target = exact ?? await api.create(trimmed);

  if (kind === 'child') {
    await api.linkCreate({ from_id: state.currentId, to_id: target.id, kind: 'child' });
  } else if (kind === 'parent') {
    await api.linkCreate({ from_id: target.id, to_id: state.currentId, kind: 'child' });
  } else {
    await api.linkCreate({ from_id: state.currentId, to_id: target.id, kind: 'jump' });
  }

  await brain.render(state.currentId);
}

async function renameCurrent() {
  if (state.currentId == null) return;
  const t = await api.getThought(state.currentId);
  const name = prompt('Rename to:', t.name);
  if (!name || !name.trim() || name === t.name) return;
  await api.update(state.currentId, { name: name.trim() });
  await brain.render(state.currentId);
  await updateTitle();
}

async function deleteCurrent() {
  if (state.currentId == null) return;
  const t = await api.getThought(state.currentId);
  if (!confirm(`Delete "${t.name}"? This also removes its links.`)) return;
  await api.remove(state.currentId);

  let next = null;
  while (state.history.length && next == null) {
    const candidate = state.history.pop();
    const exists = await api.getThought(candidate).catch(() => null);
    if (exists) next = candidate;
  }
  if (next == null) {
    const roots = await api.roots();
    next = roots[0]?.id ?? null;
  }
  state.currentId = null;
  if (next != null) {
    await selectThought(next, { pushHistory: false });
  } else {
    await editor.load(null);
    await updateTitle();
    if (state.view === 'brain') {
      document.getElementById('cy').innerHTML = '';
    }
  }
}

async function goBack() {
  if (!state.history.length) return;
  const id = state.history.pop();
  const exists = await api.getThought(id).catch(() => null);
  if (!exists) return goBack();
  await selectThought(id, { pushHistory: false });
}

// ---- search box ----
function setupSearch() {
  const input   = document.getElementById('search-input');
  const results = document.getElementById('search-results');
  let timer = null;

  input.addEventListener('input', () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { results.hidden = true; results.innerHTML = ''; return; }
    timer = setTimeout(async () => {
      const hits = await api.search(q);
      results.innerHTML = '';
      for (const h of hits) {
        const row = document.createElement('div');
        row.className = 'hit';
        row.textContent = h.name;
        row.addEventListener('mousedown', async (e) => {
          e.preventDefault();
          input.value = '';
          results.hidden = true;
          await selectThought(h.id);
          if (state.view === 'columns') await columns.reset();
        });
        results.appendChild(row);
      }
      results.hidden = hits.length === 0;
    }, 150);
  });

  input.addEventListener('blur', () => {
    setTimeout(() => { results.hidden = true; }, 100);
  });
}

// ---- wire UI ----
function setupUI() {
  for (const btn of document.querySelectorAll('.view-btn')) {
    btn.addEventListener('click', () => setView(btn.dataset.view));
  }
  document.getElementById('btn-back').addEventListener('click', goBack);
  document.getElementById('btn-add-parent').addEventListener('click', () => addLinked('parent'));
  document.getElementById('btn-add-child').addEventListener('click',  () => addLinked('child'));
  document.getElementById('btn-add-jump').addEventListener('click',   () => addLinked('jump'));
  document.getElementById('btn-rename').addEventListener('click', renameCurrent);
  document.getElementById('btn-delete').addEventListener('click', deleteCurrent);

  window.addEventListener('resize', () => {
    if (state.view === 'brain') brain.resize();
  });
}

// ---- boot ----
async function boot() {
  setupUI();
  setupSearch();

  const roots = await api.roots();
  const startId = roots[0]?.id;
  if (startId != null) {
    await selectThought(startId, { pushHistory: false });
  }
}

boot().catch(err => {
  console.error(err);
  alert(`Failed to start: ${err.message}`);
});
