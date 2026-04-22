async function req(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  if (res.status === 204) return null;
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(data?.error || res.statusText);
  return data;
}

export const api = {
  getThought:    (id)            => req('GET',    `/api/thoughts/${id}`),
  neighborhood:  (id, depth = 2) => req('GET',    `/api/thoughts/${id}/neighborhood?depth=${depth}`),
  children:      (id)            => req('GET',    `/api/thoughts/${id}/children`),
  roots:         ()              => req('GET',    `/api/thoughts/roots`),
  search:        (q)             => req('GET',    `/api/search?q=${encodeURIComponent(q)}`),
  create:        (name)          => req('POST',   `/api/thoughts`, { name }),
  update:        (id, patch)     => req('PATCH',  `/api/thoughts/${id}`, patch),
  remove:        (id)            => req('DELETE', `/api/thoughts/${id}`),
  linkCreate:    (link)          => req('POST',   `/api/links`, link),
  linkRemove:    (link)          => req('DELETE', `/api/links`, link),
};
