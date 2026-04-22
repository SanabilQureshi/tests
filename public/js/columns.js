import { api } from './api.js';

const MAX_COLUMNS = 6;

export function createColumns(containerSelector, { onSelect }) {
  const host = document.querySelector(containerSelector);

  // Each column: { parent: null | {id,name}, items: [{id,name}], selectedId: number | null }
  let columns = [];

  function render() {
    host.innerHTML = '';
    for (const col of columns) {
      const el = document.createElement('div');
      el.className = 'column';

      const header = document.createElement('div');
      header.className = 'column-header';
      header.textContent = col.parent ? col.parent.name : 'Roots';
      el.appendChild(header);

      const body = document.createElement('div');
      body.className = 'column-body';
      for (const item of col.items) {
        const row = document.createElement('div');
        row.className = 'column-row' + (item.id === col.selectedId ? ' selected' : '');
        row.textContent = item.name;
        row.addEventListener('click', () => selectInColumn(col, item));
        body.appendChild(row);
      }
      el.appendChild(body);

      const footer = document.createElement('div');
      footer.className = 'column-footer';
      const addBtn = document.createElement('button');
      addBtn.textContent = col.parent ? `+ child of ${col.parent.name}` : '+ root thought';
      addBtn.addEventListener('click', () => addToColumn(col));
      footer.appendChild(addBtn);
      el.appendChild(footer);

      host.appendChild(el);
    }
  }

  async function selectInColumn(col, item) {
    col.selectedId = item.id;
    // Truncate columns to the right of this one
    const idx = columns.indexOf(col);
    columns = columns.slice(0, idx + 1);

    const children = await api.children(item.id);
    columns.push({ parent: item, items: children, selectedId: null });

    // Slide window if exceeding MAX_COLUMNS
    if (columns.length > MAX_COLUMNS) {
      columns = columns.slice(columns.length - MAX_COLUMNS);
    }

    onSelect(item.id);
    render();
  }

  async function addToColumn(col) {
    const name = prompt(col.parent ? `New child of "${col.parent.name}":` : 'New root thought:');
    if (!name || !name.trim()) return;
    const created = await api.create(name.trim());
    if (col.parent) {
      await api.linkCreate({ from_id: col.parent.id, to_id: created.id, kind: 'child' });
    }
    col.items = col.parent ? await api.children(col.parent.id) : await api.roots();
    render();
  }

  async function reset() {
    const roots = await api.roots();
    columns = [{ parent: null, items: roots, selectedId: null }];
    render();
  }

  function resize() { /* columns are flex, no explicit resize needed */ }

  return { reset, resize };
}
