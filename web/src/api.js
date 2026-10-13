// The browser signs in with an HttpOnly session cookie (see /login); nothing secret lives in page storage.
// API keys are for scripts and are issued in the Admin portal.
export function authHeaders() {
  return {};
}
export function toLogin() {
  const next = location.pathname + location.search + location.hash;
  location.href = `/login?next=${encodeURIComponent(next)}`;
}
export async function api(path, { method = 'GET', json, form } = {}) {
  const headers = { ...authHeaders() };
  let body;
  if (json !== undefined) { headers['content-type'] = 'application/json'; body = JSON.stringify(json); }
  if (form) body = form;
  const r = await fetch(path, { method, headers, body, credentials: 'same-origin' });
  if (r.status === 401 && !path.startsWith('/api/auth/')) {
    toLogin();
    throw new Error('Signed out');
  }
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!r.ok) throw new Error(data?.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `HTTP ${r.status}`);
  return data;
}
export async function download(url, filename) {
  const r = await fetch(url, { credentials: 'same-origin' });
  if (r.status === 401) { toLogin(); return; }
  if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${r.status}`); }
  const cd = r.headers.get('content-disposition') || '';
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || filename;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(await r.blob());
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

// Signed-in user, shared by the app and the Admin portal headers.
let mePromise = null;
export function loadMe() {
  mePromise = mePromise || api('/api/me');
  return mePromise;
}
export async function signOut() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
  location.href = '/login';
}
export function userMenu(me, { admin = false } = {}) {
  const who = me.user ? (me.user.name || me.user.email) : me.actor;
  return `<div class="usermenu">
    ${!admin && me.can.admin ? '<a href="/admin">Admin</a>' : ''}
    ${admin ? '<a href="/">Back to the Factory</a>' : ''}
    <span class="who" title="${esc(me.user?.email || '')}">${esc(who)} ${`<span class="pill">${esc(me.role)}</span>`}</span>
    ${me.via === 'session' ? '<a href="#" id="signout">Sign out</a>' : ''}
  </div>`;
}
export function wireUserMenu() {
  const so = document.getElementById('signout');
  if (so) so.onclick = (ev) => { ev.preventDefault(); signOut(); };
}
