# 3D Asset Factory

A self-hosted hub for the lab: a Hunyuan3D-Assets-style interface (generate, preview, gallery,
compare, benchmark) over **local open-weight 3D models** and the existing Digital Twin pipeline.

PRD and reconciliation: Notion, Portfolio Registry → *3D Asset Factory Hub*.

## What it is, and what it is not

- **It is** the front door and job runner: inputs with provenance and classification, queued runs
  on the DGX Spark, inspection, an intake gate, side-by-side comparison, benchmark suites, exports.
- **It is not** a system of record. Engineering truth stays in VDC and the Asset Intelligence
  Registry (`vdc-3d-asset`); approved components stay in `dt-component-library`; placements stay in
  site repos (`hk6-digital-twin`). The hub links to them by ID.
- Every generated mesh is `generated-visual` / `provisional`: a **bake donor**, never geometry of
  record. The hub can mark a version `technical_validated` (intake gate pass) or `visual_validated`
  (named reviewer pass). `legal_cleared` and `approved` happen in the component library (Gate C).
- Inputs classified `internal` (VDC, site and lab photos) are refused by any pipeline that sends
  data outside Equinix systems. Hosted-tool results (Hunyuan3D 3.x, Hi3D) are import-only, and only
  when their inputs were public brand-library photos.

## Pipelines

| Adapter | Workflow | Licence | Host |
|---|---|---|---|
| TRELLIS.2-4B | image → 3D (front view of a set) | MIT | DGX Spark (ARM64 build unverified) |
| Hunyuan3D-2mv | multi-view (front/left/back/right) → 3D | Tencent Hunyuan Community (excl. EU/UK/KR) | DGX Spark |
| Hunyuan3D-2.1 | image → 3D + PBR paint | same | DGX Spark |
| TRELLIS text-xlarge | text → 3D (props, robots, people) | MIT | DGX Spark |
| img2threejs-service | parametric racks | Apache-2.0 upstream | ai-server :8099 |
| external / upload | import GLB, OBJ, STL, PLY | per source | — |

Add a model: see [docs/ADDING_A_MODEL.md](docs/ADDING_A_MODEL.md).

## Layout

```
hub/            FastAPI app, models, queue, adapters, inspection, envelope fit, exports
web/            Vite + three.js UI (gallery, asset, create, compare, jobs, benchmarks, models)
workers/gpu/    GPU worker service + Dockerfile for the DGX (one container per model family)
workers/mock/   Mock worker with the same protocol, for development without a GPU
deploy/         ai-server (hub, workers, Postgres) and dgx (GPU workers) compose files
catalog/        Initial HK6 / FB-1 catalog (genset, RPP, CRAH, chiller, UPS, battery, transformer, CT)
```

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
(cd web && npm install && npm run build)
export HUB_DATA_DIR=./data HUB_TRELLIS2_URL=http://127.0.0.1:9001 HUB_HUNYUAN_URL=http://127.0.0.1:9001
export HUB_ADMIN_EMAIL=you@equinix.com HUB_ADMIN_PASSWORD='at least 12 chars'   # first admin
uvicorn workers.mock.server:app --port 9001 &     # mock GPU worker
uvicorn hub.api:app --port 8080 &
python -m hub.seed                                 # initial catalog
python -m hub.worker --gpu                         # dispatcher (Ctrl-C to stop)
# open http://127.0.0.1:8080
```

Tests: `pip install pytest && python -m pytest -q`

## Accounts and administration

People sign in at `/login`. The homepage is only the creation workflow; users, roles, workspaces,
quotas, API keys, pipeline settings, audit, usage and health live in the Admin portal at `/admin`.
See [docs/ADMIN_AND_AUTH.md](docs/ADMIN_AND_AUTH.md), which also reviews what we took from the
Hunyuan3D site.

## Deploy

See [docs/RUNBOOK.md](docs/RUNBOOK.md).
