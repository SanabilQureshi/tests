# Personal Brain

A private, self-hosted, single-user TheBrain clone. Two views:

- **Brain** — a graph of the current thought and its neighbours up to two hops deep, paired with a rich text editor for notes.
- **Columns** — Miller-columns navigation up to six levels deep, paired with the same editor.

Thoughts are linked as **parent**, **child**, or **jump** (lateral/associative), matching TheBrain's link model.

## Run

```
npm install
npm start
```

Then open http://localhost:3000.

## Stack

- Node.js + Express backend
- better-sqlite3 (single file at `data/brain.db`)
- Cytoscape.js for the graph view
- Quill 2 for the rich text editor
- Plain ES-module frontend, no build step

## Data

Everything lives in `data/brain.db`. Back it up by copying that file.
