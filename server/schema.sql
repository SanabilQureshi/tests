PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS thoughts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  name       TEXT NOT NULL,
  notes_html TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- kind = 'child' : from_id is the parent, to_id is the child (directed)
-- kind = 'jump'  : lateral; stored with from_id < to_id (normalised server-side)
CREATE TABLE IF NOT EXISTS links (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id INTEGER NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
  to_id   INTEGER NOT NULL REFERENCES thoughts(id) ON DELETE CASCADE,
  kind    TEXT NOT NULL CHECK (kind IN ('child','jump')),
  UNIQUE(from_id, to_id, kind)
);

CREATE INDEX IF NOT EXISTS links_from ON links(from_id, kind);
CREATE INDEX IF NOT EXISTS links_to   ON links(to_id,   kind);
