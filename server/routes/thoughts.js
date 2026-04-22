import { Router } from 'express';
import {
  getThought, createThought, updateThought, deleteThought,
  childrenOf, rootThoughts, neighborhood,
} from '../db.js';

const router = Router();

router.get('/roots', (_req, res) => {
  res.json(rootThoughts());
});

router.get('/:id', (req, res) => {
  const t = getThought(Number(req.params.id));
  if (!t) return res.status(404).json({ error: 'not found' });
  res.json(t);
});

router.get('/:id/children', (req, res) => {
  res.json(childrenOf(Number(req.params.id)));
});

router.get('/:id/neighborhood', (req, res) => {
  const depth = Math.max(0, Math.min(6, Number(req.query.depth ?? 2)));
  const id = Number(req.params.id);
  const data = neighborhood(id, depth);
  if (!data.nodes.length) return res.status(404).json({ error: 'not found' });
  res.json(data);
});

router.post('/', (req, res) => {
  const name = String(req.body?.name ?? '').trim();
  if (!name) return res.status(400).json({ error: 'name required' });
  res.status(201).json(createThought(name));
});

router.patch('/:id', (req, res) => {
  const id = Number(req.params.id);
  if (!getThought(id)) return res.status(404).json({ error: 'not found' });
  const { name, notes_html } = req.body ?? {};
  res.json(updateThought(id, { name, notes_html }));
});

router.delete('/:id', (req, res) => {
  const id = Number(req.params.id);
  deleteThought(id);
  res.status(204).end();
});

export default router;
