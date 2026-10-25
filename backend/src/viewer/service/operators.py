"""Applying one operator to channels, constants or earlier results (RI-17 … RI-20)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from viewer.core import operators as ops
from viewer.core.grid import DELTA_US
from viewer.service import measurements as uc
from viewer.service.context import Services
from viewer.service.results import Result, ResultStore, UnknownHandle  # noqa: F401


@dataclass
class _Grid:
    n: int
    j_start: int
    values: np.ndarray
    shift: int  # lattice steps


def _num(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ops.OperatorError(f"Constant {x!r} is not a number") from None
    if not np.isfinite(v):
        raise ops.OperatorError("Constants must be finite numbers")
    return v


def handle_of(op: ops.Operator, inputs: list[dict], params: dict, shifts: dict[str, int]) -> str:
    """Deterministic: operator, version, inputs, parameters, modes and the shifts of the files involved (RI-20)."""
    used = sorted({i["ch"]["file"] for i in inputs if "ch" in i})
    doc = dict(op=op.name, version=op.version, inputs=inputs, params=params, shifts={f: int(shifts.get(f, 0)) for f in used})
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


def normalise(inputs: list[dict]) -> list[dict]:
    out = []
    for i in inputs:
        keys = [k for k in ("ch", "handle", "const") if k in i]
        if len(keys) != 1:
            raise ops.OperatorError("Each input must be exactly one of: ch, handle, const")
        k = keys[0]
        if k == "ch":
            c = i["ch"]
            if not all(x in c for x in ("file", "channel")):
                raise ops.OperatorError("A channel input needs file and channel")
            out.append({"ch": {"file": str(c["file"]), "channel": str(c["channel"]), "mode": c.get("mode")}})
        elif k == "handle":
            out.append({"handle": str(i["handle"])})
        else:
            out.append({"const": _num(i["const"])})
    return out


def apply(svc: Services, name: str, inputs: list[dict], params: dict | None, shifts: dict[str, int] | None) -> Result:
    op = ops.get(name)
    if svc.results is None:
        raise RuntimeError("no result store configured")
    shifts = {str(k): int(v) for k, v in (shifts or {}).items()}
    params = ops.validate_params(op, params)
    inputs = normalise(inputs)
    if len(inputs) != len(op.inputs):
        raise ops.OperatorError(f"Operator {name!r} takes {len(op.inputs)} input(s), got {len(inputs)}")
    # defaults of modes depend on the channel: resolve before hashing so the same request always has the same handle
    grids: list[_Grid | None] = []
    for spec in inputs:
        if "const" in spec:
            grids.append(None)
        elif "handle" in spec:
            r = svc.results.get(spec["handle"], svc.user)
            if r is None:
                raise UnknownHandle(spec["handle"])
            grids.append(_Grid(r.n, r.j_start, r.values, 0))
        else:
            c = spec["ch"]
            s = uc.get_series(svc, c["file"], [(c["channel"], c["mode"])])["series"][0]
            c["mode"] = s["mode"]
            grids.append(_Grid(s["n"], s["j_start"], s["values"], shifts.get(c["file"], 0)))
    real = [g for g in grids if g is not None]
    if not real:
        raise ops.OperatorError("All inputs are constants: connect at least one channel or result (RI-18)")
    if op.nature == "temporal" and any(g is None for g in grids):
        raise ops.OperatorError(f"Operator {name!r} works on time and cannot take a constant input (RI-18)")

    n_out = max(g.n for g in real)
    lo = min(g.j_start * g.n + g.shift for g in real)
    hi = max((g.j_start + g.values.size - 1) * g.n + g.shift for g in real)
    j_lo = -((-lo) // n_out)  # ceil
    j_hi = hi // n_out
    if j_hi < j_lo:
        raise ops.OperatorError("The inputs do not overlap in time")
    ks = np.arange(j_lo, j_hi + 1, dtype=np.int64) * n_out
    arrays: list[np.ndarray] = []
    for spec, g in zip(inputs, grids):
        if g is None:
            arrays.append(np.full(ks.size, spec["const"], dtype=np.float64))
            continue
        # the faster grid is sub-sampled by index: only points that exist on the channel's own grid (RI-17)
        own = ks - g.shift
        idx = own // g.n - g.j_start
        ok = (own % g.n == 0) & (idx >= 0) & (idx < g.values.size)
        a = np.full(ks.size, np.nan)
        a[ok] = g.values[idx[ok]]
        arrays.append(a)
    channel_arrays = [a for a, g in zip(arrays, grids) if g is not None]
    coincident = int(np.sum(~np.any([np.isnan(a) for a in channel_arrays], axis=0)))
    values = ops.call(op, arrays, params, n_out * DELTA_US / 1e6)
    handle = handle_of(op, inputs, params, shifts)
    res = Result(handle, n_out, int(j_lo), values, op.output == "discrete", coincident, name, {svc.user})
    svc.results.put(res)
    return res


def summarise(r: Result, bins: int = 200) -> dict:
    """Statistics and a server-made min/max sparkline for intermediate blocks (RF-29)."""
    v = r.values
    ok = ~np.isnan(v)
    count = int(ok.sum())
    stats = dict(count=count, nulls=int(v.size - count), min=None, max=None, mean=None, last=None)
    if count:
        stats.update(min=float(v[ok].min()), max=float(v[ok].max()), mean=float(v[ok].mean()), last=float(v[ok][-1]))
    spark: list[list[float | None]] = []
    if v.size:
        edges = np.linspace(0, v.size, min(bins, v.size) + 1).astype(int)[:-1]
        lo = np.fmin.reduceat(v, edges)
        hi = np.fmax.reduceat(v, edges)
        spark = [[None if np.isnan(a) else float(a), None if np.isnan(b) else float(b)] for a, b in zip(lo, hi)]
    return dict(stats=stats, sparkline=spark)


def as_series_result(r: Result) -> dict:
    """The same structure `get_series` returns, so the API encoders can send it as JSON or Arrow."""
    return dict(
        step_base_us=DELTA_US,
        series=[dict(file="~calc", channel=r.handle, n=r.n, mode="step" if r.discrete else "linear", j_start=r.j_start, values=r.values)],
    )
