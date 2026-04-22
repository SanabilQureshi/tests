import { api } from './api.js';

const TOOLBAR = [
  [{ header: [1, 2, 3, false] }],
  ['bold', 'italic', 'underline', 'strike'],
  [{ list: 'ordered' }, { list: 'bullet' }],
  ['blockquote', 'code-block', 'link'],
  ['clean'],
];

export function createEditor(hostSelector, statusSelector) {
  const quill = new Quill(hostSelector, {
    theme: 'snow',
    placeholder: 'Write notes…',
    modules: { toolbar: TOOLBAR },
  });
  const status = document.querySelector(statusSelector);

  let currentId = null;
  let saveTimer = null;
  let suppressChange = false;

  function setStatus(text) { status.textContent = text; }

  async function flush() {
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
    if (currentId == null) return;
    const html = quill.root.innerHTML;
    try {
      await api.update(currentId, { notes_html: html });
      setStatus('Saved.');
    } catch (err) {
      setStatus(`Save failed: ${err.message}`);
    }
  }

  quill.on('text-change', (_delta, _old, source) => {
    if (suppressChange || source !== 'user' || currentId == null) return;
    setStatus('Saving…');
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 500);
  });

  async function load(id) {
    await flush();
    currentId = id;
    if (id == null) {
      suppressChange = true;
      quill.setContents([]);
      suppressChange = false;
      setStatus('');
      return;
    }
    const t = await api.getThought(id);
    suppressChange = true;
    quill.root.innerHTML = t.notes_html || '';
    suppressChange = false;
    setStatus('');
  }

  window.addEventListener('beforeunload', () => { flush(); });

  return { load, flush, getCurrentId: () => currentId };
}
