import express from 'express';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import './db.js';
import thoughts from './routes/thoughts.js';
import links from './routes/links.js';
import search from './routes/search.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();

app.use(express.json({ limit: '4mb' }));

app.use('/api/thoughts', thoughts);
app.use('/api/links', links);
app.use('/api/search', search);

app.use(express.static(join(__dirname, '..', 'public')));

const port = Number(process.env.PORT ?? 3000);
app.listen(port, '127.0.0.1', () => {
  console.log(`Personal Brain running at http://127.0.0.1:${port}`);
});
