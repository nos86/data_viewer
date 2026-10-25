"""Operator registry (RI-16): pure numpy functions on aligned float64 arrays; NaN is null and propagates (RI-19).

An operator receives its inputs already on one grid (RI-17), the validated parameters and the grid step in seconds
(only temporal operators use it). Constants arrive as full arrays; the service rejects the combinations RI-18 forbids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np


class OperatorError(ValueError):
    """Invalid operator, parameter or input combination (reported as 422)."""


@dataclass(frozen=True)
class InputSpec:
    name: str
    description: str = ""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: str  # 'float' | 'int' | 'bool'
    default: float | int | bool
    min: float | None = None
    max: float | None = None
    description: str = ""


@dataclass
class Operator:
    name: str
    version: int
    description: str
    inputs: tuple[InputSpec, ...]
    params: tuple[ParamSpec, ...]
    output: str  # 'continuous' | 'discrete'
    nature: str  # 'pointwise' | 'temporal'
    deprecated: bool
    promoted_from: dict | None
    fn: Callable[[list[np.ndarray], dict[str, Any], float], np.ndarray] = field(repr=False)

    def describe(self) -> dict:
        return dict(
            name=self.name, version=self.version, description=self.description, output=self.output, nature=self.nature,
            deprecated=self.deprecated, promoted=self.promoted_from is not None,
            inputs=[dict(name=i.name, description=i.description) for i in self.inputs],
            params=[dict(name=p.name, type=p.type, default=p.default, min=p.min, max=p.max, description=p.description) for p in self.params],
        )


REGISTRY: dict[str, Operator] = {}


def operator(
    name: str,
    version: int,
    description: str,
    inputs: list[str] | list[InputSpec],
    params: list[ParamSpec] | None = None,
    output: str = "continuous",
    nature: str = "pointwise",
    deprecated: bool = False,
    promoted_from: dict | None = None,
):
    """Registers the decorated function. Names are unique; bump `version` when the result of an operator changes."""

    def deco(fn):
        if name in REGISTRY:
            raise RuntimeError(f"operator {name!r} registered twice")
        if output not in ("continuous", "discrete") or nature not in ("pointwise", "temporal"):
            raise RuntimeError(f"operator {name!r}: bad output/nature")
        specs = tuple(i if isinstance(i, InputSpec) else InputSpec(i) for i in inputs)
        REGISTRY[name] = Operator(name, version, description, specs, tuple(params or ()), output, nature, deprecated, promoted_from, fn)
        return fn

    return deco


def get(name: str) -> Operator:
    try:
        return REGISTRY[name]
    except KeyError:
        raise OperatorError(f"Unknown operator {name!r}") from None


def describe() -> list[dict]:
    return [REGISTRY[k].describe() for k in sorted(REGISTRY)]


def validate_params(op: Operator, params: dict[str, Any] | None) -> dict[str, Any]:
    """Defaults filled in, types coerced, ranges checked; unknown names are refused."""
    params = dict(params or {})
    known = {p.name: p for p in op.params}
    extra = set(params) - set(known)
    if extra:
        raise OperatorError(f"Operator {op.name!r}: unknown parameter(s) {sorted(extra)}")
    out: dict[str, Any] = {}
    for p in op.params:
        v = params.get(p.name, p.default)
        try:
            v = bool(v) if p.type == "bool" else int(v) if p.type == "int" else float(v)
        except (TypeError, ValueError):
            raise OperatorError(f"Operator {op.name!r}: parameter {p.name!r} must be {p.type}") from None
        if p.type != "bool" and ((p.min is not None and v < p.min) or (p.max is not None and v > p.max) or (isinstance(v, float) and not np.isfinite(v))):
            raise OperatorError(f"Operator {op.name!r}: parameter {p.name!r}={v} outside [{p.min}, {p.max}]")
        out[p.name] = v
    return out


def call(op: Operator, inputs: list[np.ndarray], params: dict[str, Any], step_s: float) -> np.ndarray:
    if len(inputs) != len(op.inputs):
        raise OperatorError(f"Operator {op.name!r} takes {len(op.inputs)} input(s), got {len(inputs)}")
    out = np.asarray(op.fn(inputs, params, step_s), dtype=np.float64)
    if out.shape != inputs[0].shape:
        raise RuntimeError(f"operator {op.name!r} changed the length of the series")
    return out


# ---------------------------------------------------------------------------------------------------------
# helpers


def _nanmask(*arrays: np.ndarray) -> np.ndarray:
    m = np.zeros(arrays[0].shape, dtype=bool)
    for a in arrays:
        m |= np.isnan(a)
    return m


def _truth(a: np.ndarray) -> np.ndarray:
    return np.where(np.isnan(a), np.nan, (a != 0).astype(np.float64))


# ---------------------------------------------------------------------------------------------------------
# pointwise arithmetic


@operator("add", 1, "x + y", ["x", "y"])
def _add(i, p, s):
    return i[0] + i[1]


@operator("sub", 1, "x − y", ["x", "y"])
def _sub(i, p, s):
    return i[0] - i[1]


@operator("mul", 1, "x · y", ["x", "y"])
def _mul(i, p, s):
    return i[0] * i[1]


@operator("div", 1, "x / y (null where y is 0)", ["x", "y"])
def _div(i, p, s):
    with np.errstate(divide="ignore", invalid="ignore"):
        r = i[0] / i[1]
    r[i[1] == 0] = np.nan
    return r


@operator("neg", 1, "−x", ["x"])
def _neg(i, p, s):
    return -i[0]


@operator("abs", 1, "|x|", ["x"])
def _abs(i, p, s):
    return np.abs(i[0])


@operator("sqrt", 1, "Square root (null for negative x)", ["x"])
def _sqrt(i, p, s):
    with np.errstate(invalid="ignore"):
        return np.sqrt(i[0])


@operator("pow", 1, "x raised to a constant exponent (null where undefined)", ["x"], [ParamSpec("exponent", "float", 2.0, -10, 10)])
def _pow(i, p, s):
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        r = np.power(i[0], p["exponent"])
    r[~np.isfinite(r)] = np.nan
    return r


@operator("min", 1, "Smaller of x and y", ["x", "y"])
def _min(i, p, s):
    return np.minimum(i[0], i[1])


@operator("max", 1, "Larger of x and y", ["x", "y"])
def _max(i, p, s):
    return np.maximum(i[0], i[1])


@operator("clip", 1, "Limit x to [low, high]", ["x"], [ParamSpec("low", "float", -1.0), ParamSpec("high", "float", 1.0)])
def _clip(i, p, s):
    if p["low"] > p["high"]:
        raise OperatorError("Operator 'clip': low must not exceed high")
    return np.clip(i[0], p["low"], p["high"])


# ---------------------------------------------------------------------------------------------------------
# comparisons and logic (discrete: 0 / 1)


def _compare(name: str, desc: str, f):
    @operator(name, 1, desc, ["x", "y"], output="discrete")
    def _c(i, p, s):
        r = f(i[0], i[1]).astype(np.float64)
        r[_nanmask(i[0], i[1])] = np.nan
        return r

    return _c


_compare("gt", "1 where x > y, else 0", lambda x, y: x > y)
_compare("lt", "1 where x < y, else 0", lambda x, y: x < y)
_compare("ge", "1 where x ≥ y, else 0", lambda x, y: x >= y)
_compare("le", "1 where x ≤ y, else 0", lambda x, y: x <= y)
_compare("eq", "1 where x = y, else 0", lambda x, y: x == y)


@operator("and", 1, "1 where x and y are both non-zero", ["x", "y"], output="discrete")
def _and(i, p, s):
    return np.where(_nanmask(i[0], i[1]), np.nan, ((i[0] != 0) & (i[1] != 0)).astype(np.float64))


@operator("or", 1, "1 where x or y is non-zero", ["x", "y"], output="discrete")
def _or(i, p, s):
    return np.where(_nanmask(i[0], i[1]), np.nan, ((i[0] != 0) | (i[1] != 0)).astype(np.float64))


@operator("not", 1, "1 where x is zero, else 0", ["x"], output="discrete")
def _not(i, p, s):
    return np.where(np.isnan(i[0]), np.nan, (i[0] == 0).astype(np.float64))


@operator("select", 1, "a where cond is non-zero, else b (null cond gives null)", ["cond", "a", "b"])
def _select(i, p, s):
    r = np.where(i[0] != 0, i[1], i[2])
    r[np.isnan(i[0])] = np.nan
    return r


# ---------------------------------------------------------------------------------------------------------
# temporal operators (receive the grid step)


@operator("derivative", 1, "Time derivative (central differences, units per second)", ["x"], nature="temporal")
def _derivative(i, p, s):
    x = i[0]
    r = np.full(x.shape, np.nan)
    if x.size >= 2:
        r[1:-1] = (x[2:] - x[:-2]) / (2 * s)
        r[0] = (x[1] - x[0]) / s
        r[-1] = (x[-1] - x[-2]) / s
    r[np.isnan(x)] = np.nan
    return r


@operator("integral", 1, "Running trapezoidal integral over time; intervals touching a null add nothing", ["x"],
          [ParamSpec("initial", "float", 0.0)], nature="temporal")
def _integral(i, p, s):
    x = i[0]
    inc = np.zeros(x.shape)
    if x.size >= 2:
        seg = (x[1:] + x[:-1]) * 0.5 * s
        seg[np.isnan(seg)] = 0.0
        inc[1:] = seg
    r = p["initial"] + np.cumsum(inc)
    r[np.isnan(x)] = np.nan
    return r


@operator("moving_average", 1, "Centred moving average over a time window (partial window at the ends; a null inside the window gives null)",
          ["x"], [ParamSpec("window_s", "float", 1.0, 0.0)], nature="temporal")
def _moving_average(i, p, s):
    x = i[0]
    w = max(1, int(round(p["window_s"] / s)))
    half_lo, half_hi = (w - 1) // 2, w // 2
    n = x.size
    idx = np.arange(n)
    lo = np.maximum(idx - half_lo, 0)
    hi = np.minimum(idx + half_hi + 1, n)
    bad = np.isnan(x)
    cs = np.concatenate(([0.0], np.cumsum(np.where(bad, 0.0, x))))
    cb = np.concatenate(([0], np.cumsum(bad)))
    r = (cs[hi] - cs[lo]) / (hi - lo)
    r[(cb[hi] - cb[lo]) > 0] = np.nan
    return r


@operator("delay", 1, "Delays x by a time (nulls fill the start)", ["x"], [ParamSpec("delay_s", "float", 1.0, 0.0)], nature="temporal")
def _delay(i, p, s):
    x = i[0]
    d = int(round(p["delay_s"] / s))
    r = np.full(x.shape, np.nan)
    if d == 0:
        return x.copy()
    if d < x.size:
        r[d:] = x[:-d]
    return r


# ---------------------------------------------------------------------------------------------------------
# promoted formulas (RF-33, RI-21): each carries the formula it was promoted from; a permanent test checks equivalence

MAGNITUDE_FORMULA = {
    "blocks": [
        {"id": "x", "type": "input", "name": "x"},
        {"id": "y", "type": "input", "name": "y"},
        {"id": "xx", "type": "op", "op": "mul", "inputs": ["x", "x"]},
        {"id": "yy", "type": "op", "op": "mul", "inputs": ["y", "y"]},
        {"id": "s", "type": "op", "op": "add", "inputs": ["xx", "yy"]},
        {"id": "m", "type": "op", "op": "sqrt", "inputs": ["s"]},
    ],
    "output": "m",
}


@operator("magnitude", 1, "√(x² + y²) — promoted from the formula sqrt(x·x + y·y)", ["x", "y"], promoted_from=MAGNITUDE_FORMULA)
def _magnitude(i, p, s):
    with np.errstate(invalid="ignore"):
        return np.sqrt(i[0] * i[0] + i[1] * i[1])
