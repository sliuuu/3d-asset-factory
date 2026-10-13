import { esc } from './api.js';

// Only same-site paths are followed after sign-in.
function nextUrl() {
  const n = new URLSearchParams(location.search).get('next') || '/';
  return n.startsWith('/') && !n.startsWith('//') && !n.startsWith('/login') ? n : '/';
}

async function init() {
  const me = await fetch('/api/me', { credentials: 'same-origin' });
  if (me.ok) { location.replace(nextUrl()); return; }
  const st = await fetch('/api/auth/status').then((r) => r.json()).catch(() => null);
  if (st && !st.has_users) {
    document.getElementById('setup').innerHTML = `<div class="notice">No accounts exist yet. An operator creates the first admin on the hub host:
      <div class="mono" style="margin-top:6px">python -m hub.users create --email you@equinix.com --role admin</div></div>`;
  }
}

document.getElementById('login').onsubmit = async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const err = document.getElementById('err');
  const btn = ev.target.querySelector('button');
  btn.disabled = true;
  err.innerHTML = '';
  try {
    const r = await fetch('/api/auth/login', {
      method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email: f.get('email'), password: f.get('password') }),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      throw new Error(d.detail || `HTTP ${r.status}`);
    }
    location.replace(nextUrl());
  } catch (e) {
    err.innerHTML = `<div class="notice bad">${esc(e.message)}</div>`;
    btn.disabled = false;
  }
};

init();
