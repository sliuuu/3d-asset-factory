# Runbook

## Topology

| Host | What | Port |
|---|---|---|
| ai-server.ailab.lan | hub API + UI, CPU worker, GPU dispatcher, Postgres (Dockge stack `/opt/stacks/3d-asset-factory`) | 8098 |
| dgx.ailab.lan | GPU workers: trellis2 9001, hunyuan 9002, trellis-text 9003 | 9001–9003 |
| ai-server.ailab.lan | img2threejs-service (existing) | 8099 |

The DGX hosts generation; it does not judge browser delivery. Measure viewer performance on the
target laptops (Eval Harness rule).

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
