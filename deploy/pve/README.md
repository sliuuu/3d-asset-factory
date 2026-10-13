# Hub on Proxmox

The hub (API, UI, queue, Postgres, blobs) runs in one Debian VM on the Proxmox cluster. Generation
stays on the DGX: the `worker-gpu` container only dispatches HTTP calls to Hunyuan3D at
`http://10.0.1.106:8080`, so the VM needs no GPU.

## Placement: pve4, local storage (proposed 2026-10-13, revisit at production)

From the homelab records in Notion (Ops & Changelog 2026-09-21, ADR-004):

| Node | Verdict | Why |
|---|---|---|
| pve1 | no | Hosts vyos-router VM 200 (P0) and the daily backup jobs; its Ceph osd.0 has failed. |
| pve2, pve3 | fallback | Always on, but each carries a running Ceph OSD and an OpenClaw agent VM (test-vm-01/02). |
| pve4 | **pick** | Always on (quorum needs it), no Ceph OSD, only guest is the retired openclaw-03. |
| pve5, pve6 | no | Normally powered off; pve5 has no Wake-on-LAN and pve6 currently does not wake. |

Constraints that come with any cluster node:

- **Quorum gate.** The cluster runs 4 of 6 votes with no margin (ADR-004 proposes a QDevice). If a
  node fails, `pve-guests` holds every VM at "waiting for quorum", so the hub is down until quorum
  returns. That is acceptable for a test-stage tool.
- **Use local storage, not Ceph.** Ceph has one failed OSD; keep the hub's disk off it.
- **pve4's single 1G NIC also carries corosync and the Tailscale path home.** Hub traffic (images
  in, GLBs out) is light, but don't run bulk imports through it.
- **DNS.** No `assets.ailab.lan` record yet (it needs a VyOS edit); use the IPs.

## VM (as built 2026-10-13)

VM 107 `asset-hub` on pve4 `local-lvm`: 2 vCPU, 4 GB RAM, 40 GB disk, Ubuntu 24.04, `10.10.0.107` on
SDN `vnet0`. Built with the homelab `vm-create.sh` path (clone template 9000 on pve1, migrate to pve4),
Docker from the homelab Ansible `docker` role. Hub URL: `http://10.10.0.107:8098`.

**Set the CPU type to `x86-64-v2-AES`.** Clones of template 9000 get `kvm64`, which lacks x86-64-v2;
NumPy aborts on it and the hub crash-loops.

## Deploy

```bash
git clone https://github.com/sliuuu/3d-asset-factory && cd 3d-asset-factory/deploy/pve
cp .env.example .env    # set HUB_API_TOKEN and the Postgres password in both lines
curl -s -o /dev/null -w '%{http_code}\n' http://10.0.1.106:8080/config   # expect 200
docker compose up -d --build
curl -s localhost:8098/api/health
```

The web UI asks for the hub API token on first use and remembers it per browser. To read it later:

```bash
ssh 10.10.0.107 'sudo docker exec $(sudo docker ps --format "{{.Names}}" | grep -m1 -- -hub-) printenv HUB_API_TOKEN'
```

Leaving `HUB_API_TOKEN` empty in `.env` turns the check off (LAN-only test use).

Then run the smoke test in `docs/RUNBOOK.md` ("Fast path: the live DGX Hunyuan3D").
