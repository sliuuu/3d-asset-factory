# Accounts, administration and governance

Status: implemented in the `claude/auth-admin-portal-6jfzb7` branch (P1). Supersedes the shared
`HUB_API_TOKEN` prompt on the homepage.

## 1. What changes for people

| Before | After |
|---|---|
| The homepage prompts for a shared Hub API token and keeps it in `localStorage`. | `/login` asks for an email and password. The homepage only shows the generation workflow, and anyone signed out is sent to `/login`. |
| Everyone with the token can do everything, and nothing records who did it. | Each person has a role (viewer, creator, reviewer, admin) and belongs to one or more workspaces. Every run, import, review and admin change records who did it. |
| API tokens are an environment variable. | Admins issue named, scoped, expiring API keys in `/admin`. The secret is shown once; only its hash is stored. |
| Worker URLs can only change with a redeploy. | Admins can switch a pipeline off, or point it at another worker URL, from **Models & services**, with an audit entry. |

## 2. Review of the Tencent Hunyuan3D site

The live site (`3d.hunyuan.tencent.com`, international `3d.hunyuanglobal.com`) is a JavaScript app
behind a sign-in, and it could not be rendered from the build environment. This review is built
from Tencent's own published material (Hunyuan 3D Engine launch article, Hunyuan3D Studio paper
arXiv 2509.12815, Tencent Cloud API docs) plus the publicly known layout of the site. Items marked
*(inferred)* should be confirmed against a signed-in session.

### 2.1 What the site exposes

| Area | Hunyuan3D site | Notes |
|---|---|---|
| Sign-in | Tencent account sign-in (QQ / WeChat in China, email or Google internationally) *(inferred)* | Consumer identity, no organisation model on the web app. |
| Creation | Text→3D, image→3D, multi-view→3D, sketch→3D; style presets; bbox / A-pose control | Studio adds part split, PolyGen retopology, semantic UV, texture edit, auto-rigging. |
| Asset management ("My assets" / `/assets`) | Personal creation history with thumbnails, re-download, delete, re-texture or re-run from an asset *(inferred)* | Flat personal list; no projects or folders *(inferred)*. |
| Downloads | GLB, OBJ (published); FBX, STL, USDZ in the UI *(inferred)* | |
| Community / gallery | Public showcase of others' creations, likes, remix *(inferred)* | |
| Quota | 20 free generations per day per user (published); points / credits for more | Per-user daily cap is the whole quota model on the web app. |
| API | Tencent Cloud Hunyuan 3D API: async submit / query jobs, SecretId + SecretKey via Tencent Cloud CAM, 200 trial credits, per-account concurrency limits | Key management, sub-users, billing and usage live in the Tencent Cloud console, not in the 3D site. |
| Admin | None on the 3D site itself. Governance (users, keys, billing, logs) is all in Tencent Cloud (CAM, billing, CloudAudit). | This separation is the pattern we copy: the creation site stays clean. |

### 2.2 What we take, adapt or leave out

| Capability | Decision | Where in the hub |
|---|---|---|
| Separate sign-in, creation UI with no credentials on it | **Take** | `/login`, homepage |
| Personal generation history | **Take**, plus everyone's history for admins | Jobs → "Mine"; Admin → Audit / Usage |
| Per-user daily generation quota | **Adapt**: quotas per workspace and per user, counted on GPU runs, because the DGX is shared with the LLM stack | Admin → Workspaces, Users |
| Keys and usage in a separate console (Tencent Cloud CAM) | **Adapt**: one Admin portal in the hub, since we are one team on one lab | `/admin` |
| Key scopes and sub-accounts (CAM) | **Adapt**: each key carries a role no higher than its owner's and can be pinned to one workspace, with an expiry | Admin → API keys |
| Audit trail (CloudAudit) | **Take** | Admin → Audit log |
| Concurrency limits | **Already there**: the GPU dispatcher runs one GPU job at a time | Admin → Overview shows queue depth |
| Projects / folders | **Take as workspaces** (e.g. HK6, SV11, benchmarks) | Workspace selector in the header |
| Re-run / re-texture from an asset | **Already there** (Create from an asset) | Asset page |
| Download formats | **Already there** (GLB, OBJ, FBX, USDZ, STL, PLY, blend) | Asset page |
| Model choice | **Ours is wider**: several open-weight pipelines, side-by-side compare, benchmarks | Create, Compare, Benchmarks |
| Public community gallery, likes, remix | **Leave out**: the gallery is internal, and the intake gate plus reviewer states replace popularity | — |
| Points, credits, billing | **Leave out**: replaced by quotas; no money changes hands | — |
| Consumer social sign-in | **Leave out**: local accounts now, OIDC (Entra ID / Okta) later | §4 |
| Part split, retopology, UV, rigging | **Later, as pipelines**, not admin features (each would be an adapter) | docs/ADDING_A_MODEL.md |

Our governance rules have no Hunyuan equivalent and stay: `internal` inputs never leave Equinix
systems (enforced per adapter), generated meshes are `generated-visual` bake donors, and the hub
never grants `legal_cleared` or `approved`.

## 3. Model

### 3.1 Roles and permissions

Roles are global and ordered. Each role has the permissions of the roles before it.

| Role | Can |
|---|---|
| `viewer` | browse the gallery, assets, runs, comparisons and benchmark reports; download |
| `creator` | + create assets, upload inputs, start generations and imports, build benchmark suites |
| `reviewer` | + record reviews and move versions to `technical_validated` / `visual_validated` / `deprecated` |
| `admin` | + the Admin portal: users, workspaces, quotas, API keys, models & services, audit, usage, health |

`visual_validated` still needs the named reviewer's own pass review; the reviewer is now the
signed-in user rather than a free-text name.

### 3.2 Workspaces

A workspace groups assets and runs (for example by site or programme). Users see the assets of the
workspaces they belong to; admins see all. Existing assets move into a `default` workspace on first
start, and every existing and new user is added to it unless an admin removes them. Benchmarks stay
platform-wide.

### 3.3 Quotas

- Workspace: `daily_gpu_runs` (0 = unlimited) and `max_queued` (0 = unlimited).
- User: `daily_gpu_runs` (0 = unlimited).

Checked when a generation is submitted; a run over quota is refused with HTTP 429 and the limit
that was hit. CPU runs and imports are not counted. Usage is reported per day, user, workspace and
pipeline, with GPU seconds from the worker's own timings.

### 3.4 Credentials

- **Browser sessions**: random 256-bit token in an `HttpOnly`, `SameSite=Lax` cookie (`Secure` when
  `HUB_COOKIE_SECURE=1`, which should be set once the hub is behind TLS); only the SHA-256 of the
  token is stored. Idle timeout 12 h, absolute 7 days (`HUB_SESSION_HOURS`). State-changing
  requests that carry a cookie must come from the hub's own origin.
- **Passwords**: scrypt (stdlib), per-user salt, minimum 12 characters. Five failed sign-ins from
  one address or for one account lock it out for 5 minutes.
- **API keys**: `haf_<id>_<secret>`, sent as `Authorization: Bearer …`. Stored as SHA-256. Each key
  has an owner, a role (≤ the owner's), an optional workspace, an optional expiry, last-used time
  and address. Disabling a user disables their keys. Tokens in query strings are no longer accepted.
- **Legacy `HUB_API_TOKEN`**: still accepted as a bearer token with admin rights so existing scripts
  keep working; it is shown as a warning on the Admin overview and should be removed once keys exist.

### 3.5 Audit

Append-only `audit_events`: time, actor (user or key), action, target, detail, address. Recorded for
sign-in (success and failure), sign-out, password changes, user / workspace / membership / quota
changes, key issue and revoke, model and service settings, asset create and edit, input upload,
generation submit, import, review and state change.

### 3.6 Models & services settings

Admins can, per pipeline, switch it off (it then reports itself unavailable and refuses new runs)
and override the worker URL / model settings that otherwise come from `HUB_*_URL` / `HUB_*_MODEL`
environment variables. Overrides are stored in the database and read by the API and the
dispatcher, so no redeploy is needed. Clearing an override falls back to the environment.

## 4. Bootstrapping and operations

```bash
# first admin (prompts for the password)
python -m hub.users create --email you@equinix.com --name "Your Name" --role admin
# or unattended, on first start only when no users exist
HUB_ADMIN_EMAIL=you@equinix.com HUB_ADMIN_PASSWORD='…'
python -m hub.users reset-password --email you@equinix.com
python -m hub.users list
```

`HUB_AUTH=off` turns authentication off for local development and tests only. The hub refuses to
start with `HUB_AUTH=off` unless `HUB_DEV=1` is also set.

Existing SQLite and Postgres databases are upgraded in place on start: new tables are created and
`workspace_id` / `created_by` columns are added to `assets` and `runs`.

## 5. Later

- OIDC sign-in (Entra ID) mapped to the same users and roles; local accounts kept for break-glass.
- Per-workspace roles if teams outside the lab join.
- Retention policies for blobs and runs, and storage quotas.
- Export of the audit log to the homelab logging stack.
