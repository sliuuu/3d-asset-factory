import { api, download } from './api.js';
import { createViewer, syncViewers } from './viewer.js';

const app = document.getElementById('app');
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const fmtBytes = (n) => (n == null ? '—' : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${(n / 1e3).toFixed(0)} kB`);
const num = (n, d = 0) => (n == null ? '—' : Number(n).toLocaleString(undefined, { maximumFractionDigits: d }));
const pct = (n) => (n == null ? '—' : `${(n * 100).toFixed(1)}%`);
const pill = (cls, text) => `<span class="pill ${esc(cls)}">${esc(text ?? cls)}</span>`;
const VIEWS = ['front', 'back', 'left', 'right', 'top', 'service'];
const FORMATS = ['glb', 'obj', 'fbx', 'usdz', 'stl', 'ply', 'blend'];
let disposers = [];

function cleanup() { disposers.forEach((d) => d()); disposers = []; }
function errorBox(e) { return `<div class="notice bad">${esc(e.message || e)}</div>`; }

const routes = {
  '': gallery, asset: assetPage, create, compare: comparePage, jobs, benchmarks, models,
};

async function route() {
  cleanup();
  const [path, query = ''] = location.hash.replace(/^#\/?/, '').split('?');
  const [name, id] = path.split('/');
  document.querySelectorAll('nav a').forEach((a) => a.classList.toggle('on', a.getAttribute('href') === `#/${name}`));
  const page = routes[name] || gallery;
  app.innerHTML = '<p class="muted">Loading…</p>';
  try { await page(id, new URLSearchParams(query)); } catch (e) { app.innerHTML = errorBox(e); }
}
window.addEventListener('hashchange', route);
route();

// ---------------- gallery ----------------

async function gallery(_, q) {
  const params = new URLSearchParams();
  for (const k of ['q', 'family', 'site', 'pipeline', 'evidence_class', 'state']) if (q.get(k)) params.set(k, q.get(k));
  const [assets, adapters] = await Promise.all([api(`/api/assets?${params}`), api('/api/adapters')]);
  const families = [...new Set(assets.map((a) => a.family).filter(Boolean))].sort();
  app.innerHTML = `
    <h1>Asset gallery</h1>
    <form class="row" id="filters">
      <input name="q" placeholder="Search name, family, site…" value="${esc(q.get('q') || '')}" style="min-width:260px" />
      <select name="family"><option value="">All families</option>${families.map((f) => `<option ${q.get('family') === f ? 'selected' : ''}>${esc(f)}</option>`).join('')}</select>
      <select name="pipeline"><option value="">All pipelines</option>${adapters.map((a) => `<option value="${a.name}" ${q.get('pipeline') === a.name ? 'selected' : ''}>${esc(a.label)}</option>`).join('')}</select>
      <select name="evidence_class"><option value="">All evidence</option>${['generated-visual', 'reconstruction', 'parametric', 'authored', 'library'].map((c) => `<option ${q.get('evidence_class') === c ? 'selected' : ''}>${c}</option>`).join('')}</select>
      <button>Filter</button>
      <a href="#/create"><button type="button" class="primary">+ Create</button></a>
    </form>
    <p class="muted small">${assets.length} assets. Generated meshes are provisional bake donors, never geometry of record.</p>
    <div class="grid">${assets.map(card).join('') || '<p class="muted">No assets yet.</p>'}</div>`;
  document.getElementById('filters').onsubmit = (ev) => {
    ev.preventDefault();
    const p = new URLSearchParams([...new FormData(ev.target)].filter(([, v]) => v));
    location.hash = `#/?${p}`;
  };
  // Live thumbnails only for small models; large donors (tens of MB) open on the detail page.
  const io = new IntersectionObserver((entries) => entries.forEach((en) => {
    if (!en.isIntersecting) return;
    io.unobserve(en.target);
    const { url } = en.target.dataset;
    if (url) { const v = createViewer(en.target, url, { autoRotate: true }); disposers.push(v.dispose); }
  }));
  document.querySelectorAll('.thumb[data-url]').forEach((el) => io.observe(el));
  disposers.push(() => io.disconnect());
}

function card(a) {
  const v = a.latest;
  const model = v?.artifacts.find((x) => x.role === 'model');
  const small = model && model.format === 'glb' && model.size_bytes < 8e6;
  return `<a class="card" href="#/asset/${a.id}" style="color:inherit">
    <div class="thumb" ${small ? `data-url="${model.url}"` : ''}>${v ? (small ? '' : `${fmtBytes(model?.size_bytes)} · open to view`) : 'no versions yet'}</div>
    <h3>${esc(a.name)}</h3>
    <div class="meta">${esc([a.family, a.site].filter(Boolean).join(' · '))} · ${a.version_count} version${a.version_count === 1 ? '' : 's'}</div>
    ${v ? `<div style="margin-top:6px">${pill(v.evidence_class === 'generated-visual' ? 'gen' : '', v.evidence_class)}${pill(v.intake_status, `intake ${v.intake_status}`)}${pill('', v.state)}</div>
    <div class="meta" style="margin-top:4px">${esc(v.label)} · ${num(v.metrics.triangles)} tris</div>` : ''}
  </a>`;
}

// ---------------- asset detail ----------------

async function assetPage(id, q) {
  const a = await api(`/api/assets/${id}`);
  const vid = q.get('v') || a.versions.at(-1)?.id;
  const v = a.versions.find((x) => x.id === vid);
  app.innerHTML = `
    <div class="row" style="justify-content:space-between"><h1>${esc(a.name)}</h1>
      <div class="row"><a href="#/create?asset=${a.id}"><button class="primary">Generate / import</button></a></div></div>
    <p class="muted">${esc([a.family, a.site, a.library_ref && `library: ${a.library_ref}`].filter(Boolean).join(' · '))}</p>
    ${a.description ? `<p>${esc(a.description)}</p>` : ''}
    <div class="split">
      <div>
        <div id="viewer" class="viewer">${v ? '' : '<p class="muted" style="padding:20px">No versions yet.</p>'}</div>
        <h2>Versions</h2>
        <table><tr><th></th><th>Version</th><th>Pipeline</th><th>Evidence</th><th>Intake</th><th>State</th><th>Tris</th><th>Size</th></tr>
        ${a.versions.map((x) => `<tr><td><input type="checkbox" class="cmp" value="${x.id}"></td>
          <td><a href="#/asset/${a.id}?v=${x.id}">${esc(x.label)}</a>${x.id === vid ? ' ◂' : ''}<div class="muted small">${new Date(x.created_at).toLocaleString()}</div></td>
          <td>${esc(x.pipeline)}</td><td>${pill(x.evidence_class === 'generated-visual' ? 'gen' : '', x.evidence_class)}</td>
          <td>${pill(x.intake.status, x.intake.status)}</td><td>${esc(x.state)}</td><td>${num(x.metrics.triangles)}</td>
          <td>${fmtBytes(x.artifacts[0]?.size_bytes)}</td></tr>`).join('')}</table>
        <div class="row" style="margin-top:8px"><button id="cmpbtn">Compare selected</button></div>
        <h2>Runs</h2>
        <table><tr><th>Run</th><th>Adapter</th><th>Status</th><th>Model</th><th>Error</th></tr>
        ${a.runs.map((r) => `<tr><td class="mono">${r.id}</td><td>${esc(r.adapter)}</td><td>${pill(r.status)}</td>
          <td class="small">${esc(r.worker_info.model || '')}${r.worker_info.repo_revision ? `<div class="mono muted">${esc(r.worker_info.repo_revision.slice(0, 12))}</div>` : ''}</td>
          <td class="small">${esc(r.error)}</td></tr>`).join('')}</table>
      </div>
      <div>${v ? versionPanel(a, v) : ''}${envelopePanel(a)}</div>
    </div>`;
  if (v) {
    const model = v.artifacts.find((x) => x.role === 'model');
    if (model?.format === 'glb') {
      const viewer = createViewer(document.getElementById('viewer'), model.url, { envelope: a.envelope });
      disposers.push(viewer.dispose);
    } else {
      document.getElementById('viewer').innerHTML = `<p class="muted" style="padding:20px">${esc(model?.format)} preview not supported; download below.</p>`;
    }
    wireVersionPanel(a, v);
  }
  document.getElementById('cmpbtn').onclick = () => {
    const ids = [...document.querySelectorAll('.cmp:checked')].map((c) => c.value);
    if (ids.length < 2) return alert('Pick at least two versions');
    location.hash = `#/compare?v=${ids.join(',')}`;
  };
  document.getElementById('envform').onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const env = { L: +f.get('L') || null, W: +f.get('W') || null, H: +f.get('H') || null, source: f.get('source'),
      unconfirmed: ['L', 'W', 'H'].filter((k) => f.get(`u${k}`)) };
    await api(`/api/assets/${a.id}`, { method: 'PATCH', json: { envelope: env, library_ref: f.get('library_ref') } });
    route();
  };
}

function versionPanel(a, v) {
  const m = v.metrics;
  const fit = v.intake.checks?.envelope_fit?.value;
  const checks = Object.entries(v.intake.checks || {});
  return `<div class="card">
    <h3>${esc(v.label)}</h3>
    <div>${pill(v.evidence_class === 'generated-visual' ? 'gen' : '', v.evidence_class)}${pill('', v.state)}${pill(v.intake.status, `intake ${v.intake.status}`)}</div>
    ${v.evidence_class === 'generated-visual' ? '<div class="notice">Generated geometry: use as a bake donor inside the VDC envelope. Never a source of dimensions.</div>' : ''}
    <h2>Metrics</h2>
    <table class="kv">
      <tr><td>Triangles / vertices</td><td>${num(m.triangles)} / ${num(m.vertices)}</td></tr>
      <tr><td>Watertight · winding</td><td>${m.watertight ? 'yes' : 'no'} · ${m.winding_consistent ? 'consistent' : 'inconsistent'}</td></tr>
      <tr><td>Shells (largest share)</td><td>${num(m.shells)} (${pct(m.largest_shell_share)})</td></tr>
      <tr><td>Degenerate faces</td><td>${num(m.degenerate_faces)}</td></tr>
      <tr><td>Extents L·W·H (model units)</td><td>${(m.extents_lwh || []).map((x) => num(x, 3)).join(' × ')}</td></tr>
      <tr><td>Triangle aspect p99</td><td>${num(m.triangle_aspect_p99, 1)}</td></tr>
      <tr><td>Largest map</td><td>${m.max_map_px ? `${m.max_map_px}px` : 'none'}</td></tr>
      <tr><td>UV area (overlaps counted)</td><td>${pct(m.uv_area_ratio)}</td></tr>
      <tr><td>Normal-map strength</td><td>${num(m.normal_map_strength, 4)}${m.normal_map_strength != null && m.normal_map_strength < 0.02 ? ' (almost flat)' : ''}</td></tr>
      <tr><td>Texel density</td><td>${m.texel_density_px_per_unit ? `${num(m.texel_density_px_per_unit)} px / unit` : '—'}</td></tr>
    </table>
    ${fit && fit.status !== 'skipped' ? `<h2>Envelope fit (±${fit.tolerance * 100}%)</h2>
      <p>${pill(fit.status, fit.status)} uniform scale ${num(fit.uniform_scale, 3)}; worst ${esc(fit.worst_axis)} ${(fit.worst_residual * 100).toFixed(1)}%</p>
      <table class="kv">${Object.entries(fit.residuals).map(([k, r]) => `<tr><td>${k}${fit.unconfirmed_axes.includes(k) ? ' (unconfirmed)' : ''}</td><td>${num(fit.scaled_extents_m[k], 2)} m (${(r * 100).toFixed(1)}%)</td></tr>`).join('')}</table>` : ''}
    <h2>Intake gate</h2>
    <table>${checks.map(([k, c]) => `<tr><td>${esc(k.replaceAll('_', ' '))}</td><td>${c.pass === true ? pill('pass', 'pass') : c.pass === false ? pill('fail', 'fail') : pill('', 'info')}</td><td class="small muted">${esc(c.note || '')}</td></tr>`).join('')}</table>
    <h2>Download</h2>
    <div class="row">${FORMATS.map((f) => `<button class="dl" data-f="${f}">${f.toUpperCase()}</button>`).join('')}</div>
    <p class="small muted">FBX, USDZ and .blend need Blender on the hub host; open .blend or GLB in Blender for the one-click hand-off.</p>
    <h2>Lineage</h2>
    <p class="small">${(v.lineage.derived_from || []).length ? `Derived from ${v.lineage.derived_from.map((d) => `<span class="mono">${esc(d)}</span>`).join(', ')}` : 'Root version'}${v.run_id ? ` · run <span class="mono">${esc(v.run_id)}</span>` : ''}</p>
    <p class="small muted mono">sha256 ${esc(v.artifacts[0]?.sha256)}</p>
    <h2>Review</h2>
    ${v.reviews.map((r) => `<p class="small">${pill(r.verdict, r.verdict)} <b>${esc(r.reviewer)}</b> ${r.score != null ? `score ${r.score}` : ''} — ${esc(r.notes)}</p>`).join('')}
    <form id="review">
      <div class="row"><input name="reviewer" placeholder="Reviewer" required style="flex:1" />
      <select name="verdict"><option>notes</option><option>pass</option><option>fail</option></select>
      <input name="score" type="number" step="0.1" min="0" max="10" placeholder="Score" style="width:80px" /></div>
      <div class="field" style="margin-top:6px"><label><input type="checkbox" name="logo" /> Logos or trade dress visible (must be stripped)</label>
      <label><input type="checkbox" name="nologo" /> Checked: no logos or text</label></div>
      <textarea name="notes" placeholder="Notes (close-up photo match, hidden side, scale…)"></textarea>
      <div class="row" style="margin-top:6px"><button>Add review</button>
      <select id="state"><option value="">Set state…</option><option>technical_validated</option><option>visual_validated</option><option>deprecated</option></select></div>
    </form>
  </div>`;
}

function envelopePanel(a) {
  const e = a.envelope || {};
  const u = new Set(e.unconfirmed || []);
  return `<div class="card" style="margin-top:14px"><h3>VDC envelope & links</h3>
    <form id="envform">
      <div class="row">${['L', 'W', 'H'].map((k) => `<div><label>${k} (m) <input type="checkbox" name="u${k}" ${u.has(k) ? 'checked' : ''}/> unconfirmed</label><input name="${k}" value="${e[k] ?? ''}" style="width:90px" /></div>`).join('')}</div>
      <div class="field"><label>Envelope source</label><input name="source" value="${esc(e.source || '')}" style="width:100%" /></div>
      <div class="field"><label>dt-component-library ref</label><input name="library_ref" value="${esc(a.library_ref)}" style="width:100%" /></div>
      <button>Save</button>
      <p class="small muted">Envelope values come from VDC. The hub only reads them to check donors; it never changes geometry of record.</p>
    </form></div>`;
}

function wireVersionPanel(a, v) {
  document.querySelectorAll('.dl').forEach((b) => {
    b.onclick = () => download(`/api/versions/${v.id}/download?format=${b.dataset.f}`, `${a.name}.${b.dataset.f}`).catch((e) => alert(e.message));
  });
  const form = document.getElementById('review');
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(form);
    const logo = f.get('logo') ? true : f.get('nologo') ? false : null;
    await api(`/api/versions/${v.id}/reviews`, { method: 'POST', json: {
      reviewer: f.get('reviewer'), verdict: f.get('verdict'), score: f.get('score') ? +f.get('score') : null,
      notes: f.get('notes'), logo_or_text_present: logo } });
    route();
  };
  document.getElementById('state').onchange = async (ev) => {
    if (!ev.target.value) return;
    try {
      await api(`/api/versions/${v.id}/state`, { method: 'POST', json: { state: ev.target.value, reviewer: form.reviewer.value } });
      route();
    } catch (e) { alert(e.message); ev.target.value = ''; }
  };
}

// ---------------- create ----------------

async function create(_, q) {
  const [assets, adapters] = await Promise.all([api('/api/assets'), api('/api/adapters')]);
  let tab = q.get('tab') || 'multiview';
  const pre = q.get('asset') || '';
  const tabs = [['multiview', 'Multi-view → 3D'], ['image', 'Image → 3D'], ['text', 'Text → 3D'], ['parametric', 'Parametric'], ['import', 'Import model']];
  const forTab = adapters.filter((a) => a.workflows.includes(tab) || (tab === 'multiview' && a.workflows.includes('image')));
  app.innerHTML = `
    <h1>Create</h1>
    <div class="tabs">${tabs.map(([k, l]) => `<button class="${k === tab ? 'on' : ''}" data-tab="${k}">${l}</button>`).join('')}</div>
    <form id="cf" class="split">
      <div class="card">
        <div class="field"><label>Asset</label>
          <select name="asset_id"><option value="">+ New asset…</option>${assets.map((a) => `<option value="${a.id}" ${a.id === pre ? 'selected' : ''}>${esc(a.name)} ${a.site ? `(${esc(a.site)})` : ''}</option>`).join('')}</select>
          <div class="row" id="newasset" style="margin-top:6px"><input name="new_name" placeholder="New asset name" /><input name="new_family" placeholder="family (genset, crah…)" /><input name="new_site" placeholder="site (HK6)" /></div>
        </div>
        ${tab === 'multiview' ? `<div class="field"><label>Views (4–8 recommended for equipment; Hunyuan3D-2mv uses front/left/back/right)</label>
          <div class="views">${VIEWS.map((v) => `<label class="slot"><img id="p-${v}" alt="" /><input type="file" accept="image/png,image/jpeg,image/webp" name="img-${v}" style="width:100%" />${v}</label>`).join('')}</div></div>` : ''}
        ${tab === 'image' ? `<div class="field"><label>Image</label><input type="file" accept="image/png,image/jpeg,image/webp" name="img-front" />
          <div class="notice">Single view: the hidden side will be invented. Fine for props; not for equipment donors.</div></div>` : ''}
        ${tab === 'text' ? `<div class="field"><label>Prompt</label><textarea name="prompt" placeholder="A small autonomous maintenance robot with a sensor mast"></textarea>
          <p class="small muted">Text-to-3D is for props, robots and people (Tech Spec §5.1), not infrastructure equipment.</p></div>` : ''}
        ${tab === 'parametric' ? `<div class="row"><div><label>Family</label><select name="family"><option>ai-rack</option><option>rack</option><option>orv3</option></select></div>
          <div><label>U / OU</label><input name="u" type="number" value="44" style="width:80px" /></div>
          <div><label>Width m</label><input name="width" value="0.6" style="width:80px" /></div>
          <div><label>Depth m</label><input name="depth" value="1.2" style="width:80px" /></div></div>` : ''}
        ${tab === 'import' ? `<div class="field"><label>Model file (GLB preferred; OBJ, STL, PLY accepted)</label><input type="file" name="model" accept=".glb,.gltf,.obj,.stl,.ply" required /></div>
          <div class="row"><div><label>Source</label><select name="source"><option value="upload">Lab-produced (Blender loop, FreeCAD, library, reconstruction)</option><option value="external">Hosted tool result (Hunyuan3D 3.x, Hi3D…)</option></select></div>
          <div><label>Evidence class</label><select name="evidence_class"><option value="">default</option><option>generated-visual</option><option>reconstruction</option><option>parametric</option><option>authored</option><option>library</option></select></div>
          <div><label>Tool / model</label><input name="source_tool" placeholder="Hunyuan3D 3.1 (1.5M)" /></div></div>
          <div class="field" style="margin-top:8px"><label>Derived from (version ids, comma separated)</label><input name="derived_from" style="width:100%" /></div>` : ''}
        ${tab !== 'parametric' ? `<div class="row" style="margin-top:10px">
          <div><label>Input classification</label><select name="classification"><option value="internal">internal (VDC, site, lab photos)</option><option value="public">public (brand photo library)</option></select></div>
          <div style="flex:1"><label>Provenance</label><input name="provenance" placeholder="e.g. brand library TR6, FB-1 shortlist #12" style="width:100%" /></div>
          <div style="flex:1"><label>Licence</label><input name="licence" placeholder="internal use" style="width:100%" /></div></div>` : ''}
      </div>
      <div class="card">
        ${tab === 'import' ? '<p class="muted small">Imported models get the same inspection and intake gate as generated ones. Hosted-tool results are only accepted when the inputs were public.</p>' : `
        <label>Pipelines (pick several for a side-by-side batch)</label>
        ${forTab.map((a) => `<label style="display:block;margin:6px 0;color:var(--text)"><input type="checkbox" name="adapter" value="${a.name}" ${a.available ? '' : 'disabled'} />
          <b>${esc(a.label)}</b> <span class="muted small">${esc(a.licence)} · ${esc(a.host)}</span>
          ${a.available ? '' : `<div class="small" style="color:var(--warn)">${esc(a.unavailable_reason)}</div>`}
          <div class="small muted">${esc(a.notes)}</div></label>`).join('') || '<p class="muted">No pipelines for this workflow yet.</p>'}
        <div class="row"><div><label>Seed</label><input name="seed" type="number" value="12345" style="width:110px" /></div>
        <div><label><input type="checkbox" name="texture" checked /> PBR texture</label></div></div>`}
        <div style="margin-top:14px"><button class="primary" id="go">${tab === 'import' ? 'Import' : 'Queue'}</button></div>
        <div id="out"></div>
      </div>
    </form>`;
  document.querySelectorAll('[data-tab]').forEach((b) => { b.onclick = () => { location.hash = `#/create?tab=${b.dataset.tab}${pre ? `&asset=${pre}` : ''}`; }; });
  const formEl = document.getElementById('cf');
  const sel = formEl.asset_id;
  const toggleNew = () => { document.getElementById('newasset').style.display = sel.value ? 'none' : 'flex'; };
  sel.onchange = toggleNew; toggleNew();
  formEl.querySelectorAll('input[type=file][name^=img-]').forEach((inp) => {
    inp.onchange = () => {
      const img = document.getElementById(`p-${inp.name.slice(4)}`);
      if (img && inp.files[0]) img.src = URL.createObjectURL(inp.files[0]);
    };
  });
  formEl.onsubmit = async (ev) => {
    ev.preventDefault();
    const out = document.getElementById('out');
    const go = document.getElementById('go');
    go.disabled = true;
    try {
      const f = new FormData(formEl);
      let assetId = f.get('asset_id');
      if (!assetId) {
        if (!f.get('new_name')) throw new Error('Name the new asset');
        assetId = (await api('/api/assets', { method: 'POST', json: { name: f.get('new_name'), family: f.get('new_family'), site: f.get('new_site') } })).id;
      }
      if (tab === 'import') {
        const fd = new FormData();
        for (const k of ['source', 'evidence_class', 'source_tool', 'derived_from', 'provenance', 'licence']) fd.set(k, f.get(k) || '');
        fd.set('input_classification', f.get('classification'));
        fd.set('file', f.get('model'));
        const v = await api(`/api/assets/${assetId}/import`, { method: 'POST', form: fd });
        location.hash = `#/asset/${assetId}?v=${v.id}`;
        return;
      }
      const chosen = f.getAll('adapter');
      if (!chosen.length) throw new Error('Pick at least one pipeline');
      let inputSetId = null;
      if (tab !== 'parametric') {
        const fd = new FormData();
        const views = [];
        for (const [k, val] of f.entries()) if (k.startsWith('img-') && val instanceof File && val.size) { fd.append('images', val); views.push(k.slice(4)); }
        fd.set('views', JSON.stringify(views));
        fd.set('prompt', f.get('prompt') || '');
        fd.set('classification', f.get('classification'));
        fd.set('provenance', f.get('provenance') || '');
        fd.set('licence', f.get('licence') || '');
        inputSetId = (await api('/api/inputs', { method: 'POST', form: fd })).id;
      }
      const params = { seed: +f.get('seed') || 12345, texture: !!f.get('texture') };
      if (tab === 'parametric') Object.assign(params, { family: f.get('family'), u: +f.get('u'), ou: +f.get('u'), width: +f.get('width'), depth: +f.get('depth') });
      const res = await api('/api/runs', { method: 'POST', json: { asset_id: assetId, input_set_id: inputSetId, workflow: tab, adapters: chosen, params } });
      out.innerHTML = `<h2>Queued</h2>${res.runs.map((r) => `<p>${pill(r.status)} ${esc(r.adapter)} ${r.error ? `<span class="small" style="color:var(--bad)">${esc(r.error)}</span>` : ''}</p>`).join('')}
        <p><a href="#/jobs${res.batch_id ? `?batch=${res.batch_id}` : ''}">Watch jobs</a> · <a href="#/asset/${assetId}">Open asset</a></p>`;
    } catch (e) { out.innerHTML = errorBox(e); }
    go.disabled = false;
  };
}

// ---------------- compare ----------------

async function comparePage(_, q) {
  const ids = (q.get('v') || '').split(',').filter(Boolean);
  if (ids.length < 2) {
    app.innerHTML = '<h1>Compare</h1><p class="muted">Open an asset, tick two to six versions and press “Compare selected”. Batch runs from Create land on the same asset, ready to compare.</p>';
    return;
  }
  const ref = q.get('ref') || '';
  const data = await api('/api/compare', { method: 'POST', json: { version_ids: ids, reference_version_id: ref || null } });
  const rows = data.rows;
  const metric = (label, fn) => `<tr><th>${label}</th>${rows.map((r) => `<td>${fn(r)}</td>`).join('')}</tr>`;
  app.innerHTML = `
    <h1>Compare</h1>
    <div class="row"><label>Reference for deviation</label>
      <select id="ref"><option value="">none</option>${rows.map((r) => `<option value="${r.version_id}" ${r.version_id === ref ? 'selected' : ''}>${esc(r.label)}</option>`).join('')}</select>
      <span class="small muted">Cameras are linked: orbit one, all follow.</span></div>
    <div class="compare-grid" style="margin-top:10px">${rows.map((r, i) => `<div><div id="cv${i}"></div><p class="small"><b>${esc(r.label)}</b> ${pill(r.evidence_class === 'generated-visual' ? 'gen' : '', r.evidence_class)}</p></div>`).join('')}</div>
    <h2>Metrics</h2>
    <div style="overflow-x:auto"><table>
      ${metric('Pipeline', (r) => esc(r.pipeline))}
      ${metric('Asset', (r) => esc(r.asset))}
      ${metric('Triangles', (r) => num(r.triangles))}
      ${metric('GLB size', (r) => fmtBytes(r.size_bytes))}
      ${metric('Watertight', (r) => (r.watertight ? 'yes' : 'no'))}
      ${metric('Shells (largest)', (r) => `${num(r.shells)} (${pct(r.largest_shell_share)})`)}
      ${metric('Triangle aspect p99', (r) => num(r.triangle_aspect_p99, 1))}
      ${metric('Largest map', (r) => (r.max_map_px ? `${r.max_map_px}px` : '—'))}
      ${metric('UV area', (r) => pct(r.uv_area_ratio))}
      ${metric('Normal strength', (r) => num(r.normal_map_strength, 4))}
      ${metric('Texel density', (r) => num(r.texel_density_px_per_unit))}
      ${metric('Envelope fit', (r) => (r.envelope_fit && r.envelope_fit.status !== 'skipped' ? `${pill(r.envelope_fit.status)} worst ${esc(r.envelope_fit.worst_axis)} ${(r.envelope_fit.worst_residual * 100).toFixed(1)}%` : '—'))}
      ${metric('Deviation vs ref (median / p99, % length)', (r) => (r.deviation_vs_reference ? `${r.deviation_vs_reference.median_pct.toFixed(2)} / ${r.deviation_vs_reference.p99_pct.toFixed(2)}` : '—'))}
      ${metric('Wall time', (r) => (r.seconds ? `${num(r.seconds, 1)} s` : '—'))}
      ${metric('Intake', (r) => pill(r.intake_status, r.intake_status))}
    </table></div>
    <p class="small muted">Deviation centres both meshes and samples the reference densely; it is approximate and assumes both are at the same scale.</p>`;
  const viewers = rows.map((r, i) => createViewer(document.getElementById(`cv${i}`), r.glb_url));
  viewers.forEach((v) => disposers.push(v.dispose));
  syncViewers(viewers);
  document.getElementById('ref').onchange = (ev) => { location.hash = `#/compare?v=${ids.join(',')}${ev.target.value ? `&ref=${ev.target.value}` : ''}`; };
}

// ---------------- jobs ----------------

async function jobs(_, q) {
  const batch = q.get('batch') || '';
  const render = async () => {
    const runs = await api(`/api/runs?limit=200${batch ? `&batch_id=${batch}` : ''}`);
    app.innerHTML = `<h1>Jobs ${batch ? `<span class="muted small">batch ${esc(batch)}</span>` : ''}</h1>
      <table><tr><th>Created</th><th>Run</th><th>Kind</th><th>Adapter</th><th>Workflow</th><th>Status</th><th>Asset</th><th>Detail</th></tr>
      ${runs.map((r) => `<tr><td class="small">${new Date(r.created_at).toLocaleString()}</td><td class="mono">${r.id}</td><td>${esc(r.kind)}</td>
        <td>${esc(r.adapter)}</td><td>${esc(r.params.workflow || '')}</td><td>${pill(r.status)}</td>
        <td>${r.asset_id ? `<a href="#/asset/${r.asset_id}">open</a>` : ''}</td>
        <td class="small">${esc(r.error || r.events.at(-1)?.msg || '')}</td></tr>`).join('')}</table>`;
    return runs.some((r) => r.status === 'queued' || r.status === 'running');
  };
  const active = await render();
  if (active) {
    const t = setInterval(async () => { if (!(await render())) clearInterval(t); }, 4000);
    disposers.push(() => clearInterval(t));
  }
}

// ---------------- benchmarks ----------------

async function benchmarks(id) {
  if (id) {
    const rep = await api(`/api/suites/${id}/report`);
    app.innerHTML = `<h1>${esc(rep.suite)}</h1>
      <div class="row"><button id="runsuite" class="primary">Run suite</button><span class="small muted">Queues every case × pipeline with the case's fixed inputs.</span></div>
      <table style="margin-top:12px"><tr><th>Asset</th><th>Pipeline</th><th>Tris</th><th>Watertight</th><th>Shells</th><th>Normal strength</th><th>Envelope</th><th>Deviation median / p99</th><th>Time</th><th>Reviews</th><th></th></tr>
      ${rep.rows.map((r) => `<tr><td>${esc(r.asset)}</td><td>${esc(r.pipeline)}</td>
        <td>${num(r.triangles)}</td><td>${r.version_id ? (r.watertight ? 'yes' : 'no') : '—'}</td><td>${num(r.shells)}</td><td>${num(r.normal_map_strength, 4)}</td>
        <td>${r.envelope_fit && r.envelope_fit.status !== 'skipped' ? pill(r.envelope_fit.status) : '—'}</td>
        <td>${r.deviation_vs_reference ? `${r.deviation_vs_reference.median_pct.toFixed(2)}% / ${r.deviation_vs_reference.p99_pct.toFixed(2)}%` : '—'}</td>
        <td>${r.seconds ? `${num(r.seconds, 1)} s` : '—'}</td>
        <td class="small">${(r.reviews || []).map((x) => `${esc(x.reviewer)}: ${esc(x.verdict)}${x.score != null ? ` (${x.score})` : ''}`).join('<br>')}</td>
        <td>${r.version_id ? `<a href="#/asset/${r.asset_id}?v=${r.version_id}">open</a>` : ''}</td></tr>`).join('')}</table>`;
    document.getElementById('runsuite').onclick = async () => {
      const res = await api(`/api/suites/${id}/run`, { method: 'POST' });
      location.hash = `#/jobs?batch=${res.batch_id}`;
    };
    return;
  }
  const [suites, adapters, assets] = await Promise.all([api('/api/suites'), api('/api/adapters'), api('/api/assets')]);
  app.innerHTML = `<h1>Benchmarks</h1>
    <table><tr><th>Suite</th><th>Pipelines</th><th>Cases</th></tr>${suites.map((s) => `<tr><td><a href="#/benchmarks/${s.id}">${esc(s.name)}</a><div class="small muted">${esc(s.description)}</div></td><td>${s.pipelines.map(esc).join(', ')}</td><td>${s.cases}</td></tr>`).join('')}</table>
    <h2>New suite</h2>
    <form id="sf" class="card">
      <div class="field"><label>Name</label><input name="name" value="FB-1 Arm B — generative donors" style="width:100%" /></div>
      <div class="field"><label>Description</label><input name="description" value="Genset and CRAH; same 4–8 views per tool; envelope fit, bake, score vs Blender-loop result." style="width:100%" /></div>
      <label>Pipelines</label>${adapters.filter((a) => !a.workflows.includes('import') && !a.workflows.includes('parametric')).map((a) => `<label style="color:var(--text)"><input type="checkbox" name="p" value="${a.name}" checked /> ${esc(a.label)}</label>`).join('')}
      <p class="small muted">Add cases (asset + fixed input set + optional reference version) through the API: POST /api/suites/{id}/cases. ${assets.length} assets available.</p>
      <button class="primary">Create suite</button>
    </form>`;
  document.getElementById('sf').onsubmit = async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const s = await api('/api/suites', { method: 'POST', json: { name: f.get('name'), description: f.get('description'), pipelines: f.getAll('p') } });
    location.hash = `#/benchmarks/${s.id}`;
  };
}

// ---------------- models ----------------

async function models() {
  const [adapters, health] = await Promise.all([api('/api/adapters'), api('/api/health')]);
  app.innerHTML = `<h1>Models and pipelines</h1>
    <table><tr><th>Pipeline</th><th>Workflows</th><th>Licence</th><th>Host</th><th>Evidence class</th><th>Status</th><th>Notes</th></tr>
    ${adapters.map((a) => `<tr><td><b>${esc(a.label)}</b><div class="mono muted">${a.name}</div></td><td>${a.workflows.map(esc).join(', ')}</td><td class="small">${esc(a.licence)}</td>
      <td class="small">${esc(a.host)}${a.data_egress ? '<div style="color:var(--warn)">inputs leave Equinix systems: public inputs only</div>' : ''}</td>
      <td>${pill(a.evidence_class === 'generated-visual' ? 'gen' : '', a.evidence_class)}</td>
      <td>${a.available ? pill('pass', 'ready') : `${pill('pending', 'not configured')}<div class="small muted">${esc(a.unavailable_reason)}</div>`}</td>
      <td class="small muted">${esc(a.notes)}</td></tr>`).join('')}</table>
    <h2>Export formats</h2>
    <p>${Object.entries(health.formats).map(([f, ok]) => pill(ok ? 'pass' : 'pending', `${f}${ok ? '' : ' (needs Blender)'}`)).join(' ')}</p>
    <p class="small muted">Hub ${esc(health.version)}. Add a model by adding an adapter and deploying its worker; see docs/ADDING_A_MODEL.md.</p>`;
}
