import Database from 'better-sqlite3';
import { readFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const dataDir = join(__dirname, '..', 'data');
mkdirSync(dataDir, { recursive: true });

const dbPath = process.env.BRAIN_DB || join(dataDir, 'brain.db');
export const db = new Database(dbPath);
db.pragma('journal_mode = WAL');
db.pragma('foreign_keys = ON');

const schema = readFileSync(join(__dirname, 'schema.sql'), 'utf8');
db.exec(schema);

const { count } = db.prepare('SELECT COUNT(*) AS count FROM thoughts').get();
if (count === 0) {
  db.prepare('INSERT INTO thoughts (name) VALUES (?)').run('Home');
}

export function getThought(id) {
  return db.prepare('SELECT id, name, notes_html FROM thoughts WHERE id = ?').get(id);
}

export function createThought(name) {
  const info = db.prepare('INSERT INTO thoughts (name) VALUES (?)').run(name);
  return getThought(info.lastInsertRowid);
}

export function updateThought(id, { name, notes_html }) {
  const fields = [];
  const values = [];
  if (name !== undefined)       { fields.push('name = ?');       values.push(name); }
  if (notes_html !== undefined) { fields.push('notes_html = ?'); values.push(notes_html); }
  if (!fields.length) return getThought(id);
  fields.push("updated_at = datetime('now')");
  values.push(id);
  db.prepare(`UPDATE thoughts SET ${fields.join(', ')} WHERE id = ?`).run(...values);
  return getThought(id);
}

export function deleteThought(id) {
  db.prepare('DELETE FROM thoughts WHERE id = ?').run(id);
}

export function searchThoughts(q) {
  const like = `%${q}%`;
  return db
    .prepare('SELECT id, name FROM thoughts WHERE name LIKE ? ORDER BY name LIMIT 50')
    .all(like);
}

export function rootThoughts() {
  return db
    .prepare(`
      SELECT t.id, t.name FROM thoughts t
      WHERE NOT EXISTS (SELECT 1 FROM links l WHERE l.to_id = t.id AND l.kind = 'child')
      ORDER BY t.name
    `)
    .all();
}

export function childrenOf(id) {
  return db
    .prepare(`
      SELECT t.id, t.name FROM thoughts t
      JOIN links l ON l.to_id = t.id
      WHERE l.from_id = ? AND l.kind = 'child'
      ORDER BY t.name
    `)
    .all(id);
}

export function parentsOf(id) {
  return db
    .prepare(`
      SELECT t.id, t.name FROM thoughts t
      JOIN links l ON l.from_id = t.id
      WHERE l.to_id = ? AND l.kind = 'child'
      ORDER BY t.name
    `)
    .all(id);
}

export function jumpsOf(id) {
  return db
    .prepare(`
      SELECT t.id, t.name FROM thoughts t
      JOIN links l ON (l.from_id = t.id OR l.to_id = t.id)
      WHERE (l.from_id = ? OR l.to_id = ?) AND l.kind = 'jump' AND t.id != ?
      ORDER BY t.name
    `)
    .all(id, id, id);
}

/**
 * Return all thoughts within `depth` hops of the start thought, plus edges
 * between any of those thoughts. BFS over parent, child, and jump links.
 */
export function neighborhood(startId, depth) {
  const start = getThought(startId);
  if (!start) return { nodes: [], edges: [] };

  const visited = new Map();
  visited.set(start.id, { id: start.id, name: start.name, distance: 0 });
  let frontier = [start.id];

  for (let d = 0; d < depth; d++) {
    const next = [];
    for (const id of frontier) {
      const neighbors = [
        ...parentsOf(id),
        ...childrenOf(id),
        ...jumpsOf(id),
      ];
      for (const n of neighbors) {
        if (!visited.has(n.id)) {
          visited.set(n.id, { id: n.id, name: n.name, distance: d + 1 });
          next.push(n.id);
        }
      }
    }
    frontier = next;
    if (!frontier.length) break;
  }

  const ids = [...visited.keys()];
  if (!ids.length) return { nodes: [], edges: [] };

  const placeholders = ids.map(() => '?').join(',');
  const edgesRaw = db
    .prepare(`
      SELECT id, from_id, to_id, kind FROM links
      WHERE from_id IN (${placeholders}) AND to_id IN (${placeholders})
    `)
    .all(...ids, ...ids);

  return {
    nodes: [...visited.values()],
    edges: edgesRaw.map(e => ({ id: e.id, from: e.from_id, to: e.to_id, kind: e.kind })),
  };
}

export function createLink({ from_id, to_id, kind }) {
  if (from_id === to_id) throw new Error('self-link not allowed');
  let a = from_id, b = to_id;
  if (kind === 'jump' && a > b) { [a, b] = [b, a]; }
  db.prepare('INSERT OR IGNORE INTO links (from_id, to_id, kind) VALUES (?, ?, ?)')
    .run(a, b, kind);
}

export function deleteLink({ from_id, to_id, kind }) {
  let a = from_id, b = to_id;
  if (kind === 'jump' && a > b) { [a, b] = [b, a]; }
  db.prepare('DELETE FROM links WHERE from_id = ? AND to_id = ? AND kind = ?')
    .run(a, b, kind);
}
