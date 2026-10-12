"""Envelope fit: uniform scale + axis alignment against a VDC envelope.

Rule from FB-1 Arm B: a generated donor may only be uniformly scaled. If matching the envelope
would need a non-uniform scale beyond the tolerance on any axis, the donor is rejected.
"""
from __future__ import annotations

import math

TOLERANCE = 0.02  # ±2 %


def fit(extents_lwh: tuple[float, float, float], envelope: dict, tol: float = TOLERANCE) -> dict:
    """extents_lwh: model extents as (length, width, height) in model units.
    envelope: {"L": m, "W": m, "H": m}; H may be missing or flagged unconfirmed.

    Scale is anchored on length (the dimension VDC envelopes are most reliable on), then the
    other axes are reported as residuals. A least-squares (log-space) scale is reported too so a
    reviewer can see whether a different anchor would pass.
    """
    L, W, H = (float(x) for x in extents_lwh)
    targets = {"L": envelope.get("L"), "W": envelope.get("W"), "H": envelope.get("H")}
    if not targets["L"] or L <= 0:
        return {"status": "skipped", "reason": "envelope length or model length missing"}
    scale = targets["L"] / L
    scaled = {"L": L * scale, "W": W * scale, "H": H * scale}
    residuals = {}
    for axis, target in targets.items():
        if target:
            residuals[axis] = scaled[axis] / target - 1.0
    logs = [math.log(t / m) for t, m in ((targets["L"], L), (targets["W"], W), (targets["H"], H)) if t and m > 0]
    ls_scale = math.exp(sum(logs) / len(logs)) if logs else scale
    unconfirmed = [a for a in ("L", "W", "H") if a in envelope.get("unconfirmed", [])]
    worst_axis, worst = max(residuals.items(), key=lambda kv: abs(kv[1]))
    # Unconfirmed envelope axes are reported but cannot fail the gate on their own.
    gated = {a: r for a, r in residuals.items() if a not in unconfirmed}
    passed = all(abs(r) <= tol for r in gated.values())
    return {
        "status": "pass" if passed else "fail",
        "tolerance": tol,
        "uniform_scale": scale,
        "least_squares_scale": ls_scale,
        "scaled_extents_m": scaled,
        "residuals": residuals,
        "worst_axis": worst_axis,
        "worst_residual": worst,
        "unconfirmed_axes": unconfirmed,
        "note": "Residuals on unconfirmed axes are indicative only." if unconfirmed else "",
    }
