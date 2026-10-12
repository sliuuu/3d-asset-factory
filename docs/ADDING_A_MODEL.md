# Adding a model

1. **Worker.** Add a backend branch to `workers/gpu/server.py` (or a new image) that implements
   `POST /generate` per `hub/adapters/remote.py` and returns a GLB plus `X-Worker-Info`
   (model id, `repo_revision`, seed, seconds). Add its install steps to `install_backend.sh` and a
   service to `deploy/dgx/compose.yaml`.
2. **Adapter.** Add one `RemoteGpuAdapter(...)` entry in `hub/adapters/__init__.py`: name, label,
   workflows, licence, host, URL setting, model id, accepted views. Add the URL to `hub/config.py`.
3. **Licence.** Record the licence string exactly. If inputs would leave Equinix systems, set
   `data_egress=True` so internal inputs are refused.
4. **Benchmark.** Add the adapter to the FB-1 suite and run it against the same input sets before
   anyone relies on it.

Nothing else changes: runs, versions, metrics, compare and benchmarks are model-agnostic.
