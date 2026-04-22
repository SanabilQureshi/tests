import { Router } from 'express';
import { createLink, deleteLink } from '../db.js';

const router = Router();

const VALID = new Set(['child', 'jump']);

function parse(body) {
  const from_id = Number(body?.from_id);
  const to_id = Number(body?.to_id);
  const kind = String(body?.kind ?? '');
  if (!from_id || !to_id || !VALID.has(kind)) return null;
  if (from_id === to_id) return null;
  return { from_id, to_id, kind };
}

router.post('/', (req, res) => {
  const link = parse(req.body);
  if (!link) return res.status(400).json({ error: 'invalid link' });
  try {
    createLink(link);
    res.status(201).json({ ok: true });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

router.delete('/', (req, res) => {
  const link = parse(req.body);
  if (!link) return res.status(400).json({ error: 'invalid link' });
  deleteLink(link);
  res.status(204).end();
});

export default router;
