// Admin portal: platform administration kept apart from the creation app.
import { api, esc, loadMe, userMenu, wireUserMenu } from './api.js';

const app = document.getElementById('app');
const ROLES = ['viewer', 'creator', 'reviewer', 'admin'];
const pill = (cls, text) => `<span class="pill ${esc(cls)}">${esc(text ?? cls)}</span>`;
const when = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
const num = (n, d = 0) => (n == null ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }));
const gb = (n) => `${(n / 1e9).toFixed(1)} GB`;
const quota = (used, max) => (max ? `${used} / ${max}<div class="meter"><i class="${used / max > 0.8 ? 'hot' : ''}" style="width:${Math.min(100, (used / max) * 100)}%"></i></div>` : `${used} <span class="muted small">no limit</span>`);
const errorBox = (e) => `<div class="notice bad">${esc(e.message || e)}</div>`;
let me = null;
let timer = null;

const SECTIONS = [
  ['', 'Overview', overview], ['users', 'Users & roles', users], ['workspaces', 'Workspaces & quotas', workspaces],
  ['keys', 'API keys', keys], ['services', 'Models & services', services], ['usage', 'Usage', usage],
  ['audit', 'Audit log', audit],
];

async function route() {
  clearInterval(timer);
  const [name, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const sec = SECTIONS.find(([k]) => k === name) || SECTIONS[0];
  app.innerHTML = `<nav class="adminnav">${SECTIONS.map(([k, l]) => `<a href="#/${k}" class="${k === sec[0] ? 'on' : ''}">${l}</a>`).join('')}</nav><div id="sec"><p class="muted">Loading…</p></div>`;
  const el = document.getElementById('sec');
  try { await sec[2](el, new URLSearchParams(query)); } catch (e) { el.innerHTML = errorBox(e); }
}

async function boot() {
  try { me = await loadMe(); } catch { return; }
  document.getElementById('user').innerHTML = userMenu(me, { admin: true });
  wireUserMenu();
  if (!me.can.admin) {
    app.innerHTML = '<div class="notice bad">The Admin portal needs the admin role. <a href="/">Back to the Factory</a></div>';
    return;
  }
  window.addEventListener('hashchange', route);
  route();
}
boot();

// ---------------- overview ----------------

async function overview(el) {
  const render = async () => {
    const h = await api('/api/admin/health');
    const r = h.runs;
    el.innerHTML = `
      ${h.warnings.map((w) => `<div class="notice">${esc(w)}</div>`).join('')}
      <div class="stats">
        <div class="stat"><div class="n">${num(r.queued || 0)}</div><div class="l">queued${h.oldest_queued_at ? ` · oldest ${when(h.oldest_queued_at)}` : ''}</div></div>
        <div class="stat"><div class="n">${num(r.running || 0)}</div><div class="l">running</div></div>
        <div class="stat"><div class="n">${num(r.succeeded || 0)}</div><div class="l">succeeded · last ${when(h.last_success_at)}</div></div>
        <div class="stat"><div class="n">${num((r.failed || 0) + (r.rejected || 0))}</div><div class="l">failed or rejected</div></div>
        <div class="stat"><div class="n">${num(h.users)}</div><div class="l">active users · ${num(h.active_sessions)} sessions</div></div>
        <div class="stat"><div class="n">${num(h.active_keys)}</div><div class="l">API keys not revoked</div></div>
        <div class="stat"><div class="n">${gb(h.storage.blob_bytes)}</div><div class="l">blobs · ${gb(h.storage.disk_free)} free of ${gb(h.storage.disk_total)}</div></div>
        <div class="stat"><div class="n">${esc(h.version)}</div><div class="l">hub · ${esc(h.database)}</div></div>
      </div>
      <h2>Pipelines</h2>
      <table><tr><th>Pipeline</th><th>Host</th><th>Status</th></tr>
      ${h.adapters.map((a) => `<tr><td><b>${esc(a.label)}</b> <span class="mono muted">${esc(a.name)}</span></td><td class="small">${esc(a.host)}</td>
        <td>${a.available ? pill('pass', 'ready') : `${pill(a.enabled ? 'pending' : 'fail', a.enabled ? 'not configured' : 'switched off')} <span class="small muted">${esc(a.unavailable_reason)}</span>`}</td></tr>`).join('')}</table>
      <div class="row" style="margin-top:8px"><button id="probe">Check worker reachability</button><span id="probes" class="small"></span></div>
      <h2>Running now</h2>
      ${h.running.length ? `<table><tr><th>Run</th><th>Pipeline</th><th>Worker</th><th>Started</th><th>Lease until</th></tr>
        ${h.running.map((x) => `<tr><td class="mono">${esc(x.id)}</td><td>${esc(x.adapter)}</td><td class="small">${esc(x.lease_owner)}</td><td class="small">${when(x.started_at)}</td><td class="small">${when(x.lease_until)}</td></tr>`).join('')}</table>` : '<p class="muted small">Nothing running.</p>'}`;
    document.getElementById('probe').onclick = async (ev) => {
      ev.target.disabled = true;
      const out = document.getElementById('probes');
      out.textContent = 'checking…';
      try {
        const p = await api('/api/admin/health?probe=1');
        out.innerHTML = p.probes.map((x) => `${pill(x.reachable ? 'pass' : 'fail', x.reachable ? `HTTP ${x.status}` : x.error)} <span class="mono">${esc(x.key)}</span>`).join(' ') || 'No worker URLs configured.';
      } catch (e) { out.innerHTML = errorBox(e); }
      ev.target.disabled = false;
    };
  };
  await render();
  timer = setInterval(() => render().catch(() => {}), 15000);
}

// ---------------- users ----------------

async function users(el) {
  const [list, spaces] = await Promise.all([api('/api/admin/users'), api('/api/admin/workspaces')]);
  const wsName = Object.fromEntries(spaces.map((w) => [w.id, w.slug]));
  el.innerHTML = `
    <h2>Add a user</h2>
    <form id="nu" class="card row">
      <input name="email" type="email" placeholder="name@equinix.com" required style="min-width:240px" />
      <input name="name" placeholder="Display name" />
      <select name="role">${ROLES.map((r) => `<option ${r === 'creator' ? 'selected' : ''}>${r}</option>`).join('')}</select>
      <select name="ws" multiple size="${Math.min(4, spaces.length)}" title="Workspaces">${spaces.map((w) => `<option value="${w.id}" ${w.slug === 'default' ? 'selected' : ''}>${esc(w.name)}</option>`).join('')}</select>
      <input name="daily" type="number" min="0" placeholder="GPU runs/day (0 = no limit)" style="width:200px" />
      <button class="primary">Create</button>
      <span class="small muted">A temporary password is generated and shown once.</span>
    </form>
    <div id="msg"></div>
    <h2>Users</h2>
    <div class="scroll"><table><tr><th>User</th><th>Role</th><th>Workspaces</th><th>GPU runs today / limit</th><th>Keys</th><th>Last sign-in</th><th>Status</th><th></th></tr>
    ${list.map((u) => `<tr data-id="${u.id}">
      <td><b>${esc(u.name || u.email)}</b><div class="small muted">${esc(u.email)}</div></td>
      <td><select class="role">${ROLES.map((r) => `<option ${r === u.role ? 'selected' : ''}>${r}</option>`).join('')}</select></td>
      <td><select class="ws" multiple size="${Math.min(3, spaces.length)}">${spaces.map((w) => `<option value="${w.id}" ${u.workspace_ids.includes(w.id) ? 'selected' : ''}>${esc(w.slug)}</option>`).join('')}</select>
        ${u.role === 'admin' ? '<div class="small muted">admins see all</div>' : ''}</td>
      <td>${u.gpu_runs_today} / <input class="daily" type="number" min="0" value="${u.daily_gpu_runs}" style="width:70px" /></td>
      <td>${u.active_keys}</td><td class="small">${when(u.last_login_at)}</td>
      <td>${u.active ? pill('pass', 'active') : pill('fail', 'disabled')}</td>
      <td class="row"><button class="save">Save</button><button class="reset">Reset password</button><button class="signout">Sign out</button>
        <button class="toggle">${u.active ? 'Disable' : 'Enable'}</button></td></tr>`).join('')}</table></div>
    <p class="small muted">Roles: viewer browses and downloads; creator also generates, imports and builds benchmarks; reviewer also records reviews and sets validation states; admin also runs this portal. Workspaces: ${Object.values(wsName).map(esc).join(', ')}.</p>`;
  const msg = document.getElementById('msg');
  const show = (html) => { msg.innerHTML = html; };
  document.getElementById('nu').onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      const u = await api('/api/admin/users', { method: 'POST', json: {
        email: f.get('email'), name: f.get('name'), role: f.get('role'), workspace_ids: f.getAll('ws'),
        daily_gpu_runs: +f.get('daily') || 0 } });
      await users(el);
      document.getElementById('msg').innerHTML = secretBox(`Temporary password for ${u.email}`, u.temporary_password, 'Send it to them privately; they can change it after signing in.');
    } catch (e) { show(errorBox(e)); }
  };
  el.querySelectorAll('tr[data-id]').forEach((tr) => {
    const id = tr.dataset.id;
    const u = list.find((x) => x.id === id);
    const act = async (fn) => { try { await fn(); } catch (e) { show(errorBox(e)); } };
    tr.querySelector('.save').onclick = () => act(async () => {
      await api(`/api/admin/users/${id}`, { method: 'PATCH', json: {
        role: tr.querySelector('.role').value, daily_gpu_runs: +tr.querySelector('.daily').value || 0,
        workspace_ids: [...tr.querySelector('.ws').selectedOptions].map((o) => o.value) } });
      await users(el);
      document.getElementById('msg').innerHTML = `<div class="notice">Saved ${esc(u.email)}.</div>`;
    });
    tr.querySelector('.toggle').onclick = () => act(async () => {
      if (u.active && !confirm(`Disable ${u.email}? Their sessions end and their API keys stop working.`)) return;
      await api(`/api/admin/users/${id}`, { method: 'PATCH', json: { active: !u.active } });
      await users(el);
    });
    tr.querySelector('.reset').onclick = () => act(async () => {
      if (!confirm(`Reset the password for ${u.email}? They will be signed out.`)) return;
      const r = await api(`/api/admin/users/${id}/reset-password`, { method: 'POST' });
      show(secretBox(`New temporary password for ${u.email}`, r.temporary_password, 'Shown once.'));
    });
    tr.querySelector('.signout').onclick = () => act(async () => {
      await api(`/api/admin/users/${id}/sign-out`, { method: 'POST' });
      show(`<div class="notice">${esc(u.email)} was signed out everywhere.</div>`);
    });
  });
}

function secretBox(title, secret, note) {
  return `<div class="secret"><b>${esc(title)}</b><code>${esc(secret)}</code><span class="small muted">${esc(note)}</span>
    <div><button type="button" onclick="navigator.clipboard.writeText(this.closest('.secret').querySelector('code').textContent)">Copy</button></div></div>`;
}

// ---------------- workspaces ----------------

async function workspaces(el) {
  const list = await api('/api/admin/workspaces');
  el.innerHTML = `
    <h2>New workspace</h2>
    <form id="nw" class="card row">
      <input name="slug" placeholder="slug (hk6)" required pattern="[a-z0-9\\-]+" />
      <input name="name" placeholder="Name (HK6 Digital Twin)" required />
      <input name="description" placeholder="Description" style="flex:1" />
      <input name="daily" type="number" min="0" placeholder="GPU runs/day" style="width:130px" />
      <input name="queued" type="number" min="0" placeholder="Max queued" style="width:120px" />
      <button class="primary">Create</button>
    </form>
    <div id="msg"></div>
    <h2>Workspaces</h2>
    <table><tr><th>Workspace</th><th>Members</th><th>Assets</th><th>GPU runs today</th><th>Daily GPU limit</th><th>Queued / limit</th><th></th></tr>
    ${list.map((w) => `<tr data-id="${w.id}"><td><b>${esc(w.name)}</b> <span class="mono muted">${esc(w.slug)}</span><div class="small muted">${esc(w.description)}</div></td>
      <td>${w.members}</td><td>${w.assets}</td><td>${quota(w.gpu_runs_today, w.daily_gpu_runs)}</td>
      <td><input class="daily" type="number" min="0" value="${w.daily_gpu_runs}" style="width:80px" /></td>
      <td>${w.queued} / <input class="queued" type="number" min="0" value="${w.max_queued}" style="width:70px" /></td>
      <td><button class="save">Save</button></td></tr>`).join('')}</table>
    <p class="small muted">0 means no limit. GPU runs are counted per UTC day; imports and CPU runs are not counted. The GPU dispatcher still runs one GPU job at a time because the DGX is shared with the LLM stack. Members are managed on the Users page.</p>`;
  const msg = document.getElementById('msg');
  document.getElementById('nw').onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      await api('/api/admin/workspaces', { method: 'POST', json: { slug: f.get('slug'), name: f.get('name'), description: f.get('description'),
        daily_gpu_runs: +f.get('daily') || 0, max_queued: +f.get('queued') || 0 } });
      await workspaces(el);
    } catch (e) { msg.innerHTML = errorBox(e); }
  };
  el.querySelectorAll('tr[data-id]').forEach((tr) => {
    tr.querySelector('.save').onclick = async () => {
      try {
        await api(`/api/admin/workspaces/${tr.dataset.id}`, { method: 'PATCH', json: {
          daily_gpu_runs: +tr.querySelector('.daily').value || 0, max_queued: +tr.querySelector('.queued').value || 0 } });
        await workspaces(el);
      } catch (e) { msg.innerHTML = errorBox(e); }
    };
  });
}

// ---------------- API keys ----------------

async function keys(el) {
  const [list, people, spaces] = await Promise.all([api('/api/admin/keys'), api('/api/admin/users'), api('/api/admin/workspaces')]);
  el.innerHTML = `
    <h2>Issue a key</h2>
    <form id="nk" class="card row">
      <input name="name" placeholder="What uses it (bench runner, Blender add-on…)" required style="min-width:260px" />
      <select name="user_id" title="The key acts as this user">${people.filter((u) => u.active).map((u) => `<option value="${u.id}" ${u.id === me.user?.id ? 'selected' : ''}>${esc(u.email)} (${u.role})</option>`).join('')}</select>
      <select name="role">${ROLES.map((r) => `<option ${r === 'creator' ? 'selected' : ''}>${r}</option>`).join('')}</select>
      <select name="workspace_id"><option value="">Owner's workspaces</option>${spaces.map((w) => `<option value="${w.id}">${esc(w.name)} only</option>`).join('')}</select>
      <select name="expires"><option value="30">30 days</option><option value="90" selected>90 days</option><option value="365">1 year</option><option value="0">No expiry</option></select>
      <button class="primary">Issue</button>
    </form>
    <div id="msg"></div>
    <h2>Keys</h2>
    <div class="scroll"><table><tr><th>Name</th><th>Key</th><th>Acts as</th><th>Role</th><th>Workspace</th><th>Created</th><th>Expires</th><th>Last used</th><th>State</th><th></th></tr>
    ${list.map((k) => `<tr><td><b>${esc(k.name)}</b></td><td class="mono">${esc(k.prefix)}</td><td class="small">${esc(k.owner)}</td><td>${esc(k.role)}</td>
      <td class="small">${esc(spaces.find((w) => w.id === k.workspace_id)?.slug || 'owner’s')}</td><td class="small">${when(k.created_at)}</td><td class="small">${when(k.expires_at)}</td>
      <td class="small">${when(k.last_used_at)}${k.last_used_ip ? `<div class="muted">${esc(k.last_used_ip)}</div>` : ''}</td>
      <td>${pill(k.state === 'active' ? 'pass' : 'fail', k.state)}</td>
      <td>${k.state === 'active' || k.state === 'owner disabled' ? `<button data-revoke="${k.id}">Revoke</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="10" class="muted">No keys yet.</td></tr>'}</table></div>
    <p class="small muted">Scripts send <span class="mono">Authorization: Bearer haf_…</span>. A key never has more rights than the user it acts as, and stops working when that user is disabled. Only a hash is stored, so a lost key is revoked and reissued.</p>`;
  const msg = document.getElementById('msg');
  document.getElementById('nk').onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      const k = await api('/api/admin/keys', { method: 'POST', json: { name: f.get('name'), user_id: f.get('user_id'), role: f.get('role'),
        workspace_id: f.get('workspace_id') || null, expires_days: +f.get('expires') || null } });
      await keys(el);
      document.getElementById('msg').innerHTML = secretBox(`Key "${k.name}"`, k.secret, 'Copy it now. It is not shown again.');
    } catch (e) { msg.innerHTML = errorBox(e); }
  };
  el.querySelectorAll('[data-revoke]').forEach((b) => {
    b.onclick = async () => {
      if (!confirm('Revoke this key? Anything using it stops working at once.')) return;
      try { await api(`/api/admin/keys/${b.dataset.revoke}`, { method: 'DELETE' }); await keys(el); } catch (e) { msg.innerHTML = errorBox(e); }
    };
  });
}

// ---------------- models & services ----------------

async function services(el) {
  const st = await api('/api/admin/settings');
  el.innerHTML = `
    <div id="msg"></div>
    <h2>Pipelines</h2>
    <table><tr><th>On</th><th>Pipeline</th><th>Workflows</th><th>Licence</th><th>Host</th><th>Status</th></tr>
    ${st.adapters.map((a) => `<tr><td><input type="checkbox" class="on" value="${a.name}" ${a.enabled ? 'checked' : ''} /></td>
      <td><b>${esc(a.label)}</b><div class="mono muted">${esc(a.name)}</div></td><td class="small">${a.workflows.map(esc).join(', ')}</td>
      <td class="small">${esc(a.licence)}</td><td class="small">${esc(a.host)}${a.data_egress ? '<div style="color:var(--warn)">public inputs only</div>' : ''}</td>
      <td>${a.available ? pill('pass', 'ready') : `${pill('pending', 'unavailable')}<div class="small muted">${esc(a.unavailable_reason)}</div>`}</td></tr>`).join('')}</table>
    <h2>Worker endpoints and models</h2>
    <table><tr><th>Setting</th><th>From environment</th><th>Override</th><th>Changed</th></tr>
    ${st.fields.map((f) => `<tr><td class="mono">${esc(f.key)}<div class="muted">${esc(f.env)}</div></td><td class="mono small">${esc(f.env_value) || '<span class="muted">unset</span>'}</td>
      <td><input class="ov" data-key="${esc(f.key)}" value="${esc(f.override ?? '')}" placeholder="use environment" style="width:100%" /></td>
      <td class="small muted">${f.updated_by ? `${esc(f.updated_by)}<br>${when(f.updated_at)}` : ''}</td></tr>`).join('')}</table>
    <div class="row" style="margin-top:10px"><button class="primary" id="save">Save changes</button>
      <span class="small muted">Takes effect for new runs straight away, in the API and the GPU dispatcher. An empty override falls back to the environment. A switched-off pipeline refuses new runs and fails queued ones.</span></div>`;
  document.getElementById('save').onclick = async () => {
    const msg = document.getElementById('msg');
    const all = st.adapters.map((a) => a.name);
    const on = new Set([...el.querySelectorAll('.on:checked')].map((c) => c.value));
    const ov = {};
    el.querySelectorAll('.ov').forEach((i) => {
      const before = st.fields.find((f) => f.key === i.dataset.key).override ?? '';
      if (i.value.trim() !== before) ov[i.dataset.key] = i.value.trim() || null;
    });
    try {
      await api('/api/admin/settings', { method: 'PUT', json: { disabled_adapters: all.filter((n) => !on.has(n)), overrides: ov } });
      await services(el);
      document.getElementById('msg').innerHTML = '<div class="notice">Saved.</div>';
    } catch (e) { msg.innerHTML = errorBox(e); }
  };
}

// ---------------- usage ----------------

async function usage(el, q) {
  const days = +q.get('days') || 30;
  const u = await api(`/api/admin/usage?days=${days}`);
  const table = (title, rows) => `<div class="card"><h3>${title}</h3><table><tr><th></th><th>Runs</th><th>GPU time</th></tr>
    ${rows.map((r) => `<tr><td>${esc(r.name)}</td><td>${num(r.runs)}</td><td>${num(r.gpu_seconds / 60, 1)} min</td></tr>`).join('') || '<tr><td colspan="3" class="muted">No runs.</td></tr>'}</table></div>`;
  el.innerHTML = `
    <div class="row"><span class="muted small">Generation runs in the last</span>${[7, 30, 90].map((d) => `<a href="#/usage?days=${d}"><button class="${d === days ? 'on' : ''}">${d} days</button></a>`).join('')}</div>
    <div class="grid" style="margin-top:12px">${table('By user', u.by_user)}${table('By workspace', u.by_workspace)}${table('By pipeline', u.by_pipeline)}</div>
    <h2>Generation history</h2>
    <div class="scroll"><table><tr><th>Day (UTC)</th><th>User</th><th>Workspace</th><th>Pipeline</th><th>Runs</th><th>Succeeded</th><th>Failed</th><th>Rejected</th><th>GPU time</th></tr>
    ${u.rows.map((r) => `<tr><td>${esc(r.day)}</td><td>${esc(r.user)}</td><td>${esc(r.workspace)}</td><td>${esc(r.pipeline)}</td><td>${r.runs}</td><td>${r.succeeded}</td><td>${r.failed}</td><td>${r.rejected}</td><td>${num(r.gpu_seconds / 60, 1)} min</td></tr>`).join('') || '<tr><td colspan="9" class="muted">No runs in this window.</td></tr>'}</table></div>
    <p class="small muted">GPU time is the worker's own timing for each run. Each run's full record is on the Jobs page of the Factory.</p>`;
}

// ---------------- audit ----------------

async function audit(el, q) {
  const params = new URLSearchParams();
  for (const k of ['action', 'actor', 'target_id', 'before_id']) if (q.get(k)) params.set(k, q.get(k));
  const rows = await api(`/api/admin/audit?limit=200&${params}`);
  el.innerHTML = `
    <form id="af" class="row">
      <select name="action"><option value="">All actions</option>${['auth', 'user', 'key', 'workspace', 'settings', 'asset', 'input', 'run', 'version', 'suite'].map((a) => `<option ${q.get('action') === a ? 'selected' : ''}>${a}</option>`).join('')}</select>
      <input name="actor" placeholder="Actor contains…" value="${esc(q.get('actor') || '')}" />
      <input name="target_id" placeholder="Target id" value="${esc(q.get('target_id') || '')}" />
      <button>Filter</button>
    </form>
    <div class="scroll"><table style="margin-top:10px"><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th><th>Address</th></tr>
    ${rows.map((e) => `<tr><td class="small">${when(e.at)}</td><td class="small">${esc(e.actor)}</td><td>${pill(/failed|blocked|revoke|disable/.test(e.action) ? 'fail' : '', e.action)}</td>
      <td class="small">${esc(e.target_type)} <span class="mono">${esc(e.target_id)}</span></td>
      <td class="mono small">${esc(JSON.stringify(e.detail)).slice(0, 300)}</td><td class="small">${esc(e.ip)}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">Nothing recorded.</td></tr>'}</table></div>
    ${rows.length === 200 ? `<p><a href="#/audit?${new URLSearchParams({ ...Object.fromEntries(params), before_id: rows.at(-1).id })}">Older</a></p>` : ''}
    <p class="small muted">Append-only. Sign-ins (including failures), account, key, workspace and setting changes, uploads, generations, imports, reviews and state changes.</p>`;
  document.getElementById('af').onsubmit = (ev) => {
    ev.preventDefault();
    const p = new URLSearchParams([...new FormData(ev.target)].filter(([, v]) => v));
    location.hash = `#/audit?${p}`;
  };
}
