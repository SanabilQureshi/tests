import { Router } from 'express';
import { searchThoughts } from '../db.js';

const router = Router();

router.get('/', (req, res) => {
  const q = String(req.query.q ?? '').trim();
  if (!q) return res.json([]);
  res.json(searchThoughts(q));
});

export default router;
