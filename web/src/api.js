// Token lives in localStorage only as a per-browser convenience; the server enforces it.
export function token() {
  try { return localStorage.getItem('hubToken') || ''; } catch { return ''; }
}
export function setToken(t) {
  try { localStorage.setItem('hubToken', t); } catch { /* private mode */ }
}
export function authHeaders() {
  const t = token();
  return t ? { authorization: `Bearer ${t}` } : {};
}
export async function api(path, { method = 'GET', json, form } = {}) {
  const headers = { ...authHeaders() };
  let body;
  if (json !== undefined) { headers['content-type'] = 'application/json'; body = JSON.stringify(json); }
  if (form) body = form;
  const r = await fetch(path, { method, headers, body });
  if (r.status === 401) {
    const t = prompt('Hub API token');
    if (t) { setToken(t); return api(path, { method, json, form }); }
  }
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new Error(data?.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `HTTP ${r.status}`);
  return data;
}
export async function download(url, filename) {
  const r = await fetch(url, { headers: authHeaders() });
  if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${r.status}`); }
  const cd = r.headers.get('content-disposition') || '';
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || filename;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob());
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
