# Runbook

## Topology

| Host | What | Port |
|---|---|---|
| ai-server.ailab.lan | hub API + UI, CPU worker, GPU dispatcher, Postgres (Dockge stack `/opt/stacks/3d-asset-factory`) | 8098 |
| dgx.ailab.lan | GPU workers: trellis2 9001, hunyuan 9002, trellis-text 9003 | 9001–9003 |
| ai-server.ailab.lan | img2threejs-service (existing) | 8099 |

The DGX hosts generation; it does not judge browser delivery. Measure viewer performance on the
target laptops (Eval Harness rule).

## Fast path: the live DGX Hunyuan3D (current plan, 2026-10-13)

TRELLIS.2 is deferred. Hunyuan3D-2mv has been running on the DGX since 2026-10-12. It uses Tencent's
`gradio_app.py` from `~/3dstack` (venv `envs/hunyuan3d`) in tmux session `hyapp`, at
`--host 10.0.1.106 --port 8080 --disable_tex`, so it produces shape only (no texture).

1. Check it is up: `curl -s -o /dev/null -w '%{http_code}' http://10.0.1.106:8080/config` should return 200.
   It runs in tmux, not systemd, so after a DGX reboot restart it by hand from `~/3dstack/README.md`.
2. `HUB_HUNYUAN_GRADIO_URL` is already set in `deploy/ai-server/compose.yaml`. The adapter reads the
   app's endpoint list (`/shape_generation`) and fills the multi-view inputs it finds there.
3. Bring up the hub (step 5 below). The Models page shows "Hunyuan3D-2mv (DGX, live)".
4. Smoke test: Create → Multi-view, with front/left/back/right public brand-library photos. Tick that
   adapter and Queue. Expect about 2 minutes per run and about 6 GB of GPU memory (3dstack README).
5. Memory: qwen3:30b-a3b takes about 45 GB when Ollama reloads it. That leaves roughly 13 GB available,
   which is enough for 2mv but not for TRELLIS.2.

An api_server.py adapter (`HUB_HUNYUAN_API_URL`) also exists, for a Hunyuan3D started that way.

## First deployment checklist (P1)

1. **DGX memory.** `nvidia-smi` and `docker ps`. The GB10's 128 GB is shared with Ollama, vLLM and
   ComfyUI. Stop vLLM (`docker compose -f ~/docker/vllm/docker-compose.yml down`) before the first
   TRELLIS.2 build if headroom is tight.
2. **Disk.** `df -h`; create `/data/3d-models` for weights (tens of GB per family).
3. **Build one worker at a time** on the DGX:
   `docker compose -f deploy/dgx/compose.yaml build trellis2`. The native extensions compile here;
   this is the first real test of the ARM64 assumption. Record the outcome in Notion (Ops & Changelog).
   If it fails, build the same image on an x86 GPU host and point `HUB_TRELLIS2_URL` at it.
4. `docker compose -f deploy/dgx/compose.yaml up -d trellis2`, then `curl dgx.ailab.lan:9001/health`.
5. On ai-server: copy `deploy/ai-server/.env.example` to `.env` and set secrets, then
   `docker compose -f deploy/ai-server/compose.yaml up -d --build`, then
   `docker compose -f deploy/ai-server/compose.yaml exec hub python -m hub.seed`.
6. Smoke test: Create → Multi-view, four public brand-library views of the genset, tick TRELLIS.2
   and Hunyuan3D-2mv, Queue. Jobs go queued → running → succeeded; then compare the versions.

## Operations

- Health: `GET /api/health` (adapter availability, export formats).
- Stuck run: runs hold a lease (`HUB_LEASE_SECONDS`, default 900 s); an expired lease is re-claimed.
- Free DGX memory: `curl -X POST dgx.ailab.lan:9001/unload`. Workers also unload after 10 idle minutes.
- FBX / USDZ / .blend exports need Blender in the hub image (`BLENDER_BIN`); without it they report
  unavailable rather than producing a lossy substitute.
- Backups: Postgres volume `hub-pg` and blob volume `hub-data` (content-addressed, SHA-256 keyed).

## Governance

- Never classify VDC or site photos as `public`. The egress guard trusts the classification.
- Background removal uses BiRefNet (MIT). Do not swap in RMBG-2.0 (non-commercial).
- Generated outputs keep logos until a reviewer strips them; tick "logos visible" on review.
