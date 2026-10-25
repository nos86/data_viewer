"""Master lattice and per-channel grids. Pure numpy; all time arithmetic is integer (RI-09)."""
from __future__ import annotations

import numpy as np

DELTA_US = 100_000  # master lattice step Δ


def next_n(n: int) -> int:
    """Next element of the divisibility chain 1, 2, 10, 20, 100, 200, ... (RI-10)."""
    s = str(n)
    return n * 2 if s[0] == "1" and set(s[1:]) <= {"0"} else n * 5


def assign_n(period_us: int | None, event: bool = False) -> int:
    """Smallest chain n >= 0.9 * period / Δ; never upscales (RI-10, RI-13)."""
    if event or period_us is None or period_us <= 0:
        return 1
    n = 1
    while 10 * n * DELTA_US < 9 * period_us:
        n = next_n(n)
    return n


def j_max(end_us: int, n: int) -> int:
    """Last grid index with k*Δ <= measurement end (end relative to t0)."""
    return end_us // (n * DELTA_US)


def _grid_times(j_start: int, j_end: int, n: int) -> np.ndarray:
    return np.arange(j_start, j_end + 1, dtype=np.int64) * (n * DELTA_US)


def _hole_mask(t: np.ndarray, tk: np.ndarray, max_gap_us: int) -> np.ndarray:
    """Grid points that must be null (RI-12): strictly inside an interval between consecutive
    samples longer than max_gap_us, or after the last sample. Points coinciding with a sample are kept."""
    idx = np.searchsorted(t, tk, side="right") - 1
    valid = idx >= 0
    safe = np.where(valid, idx, 0)
    exact = valid & (t[safe] == tk)
    nxt = np.minimum(safe + 1, t.size - 1)
    interval = np.where(safe + 1 < t.size, t[nxt] - t[safe], np.iinfo(np.int64).max)
    return valid & ~exact & (interval > max_gap_us)


def step_grid(
    t_rel_us: np.ndarray,
    values: np.ndarray,
    n: int,
    j_start: int,
    j_end: int,
    max_gap_us: int | None,
) -> np.ndarray:
    """Step mode on the grid k = j*n for j in [j_start, j_end] (inclusive).

    value = last sample with t <= t_k (join_asof backward); NaN before the first sample.
    max_gap_us is None for event channels (no limit, the last value is held).
    """
    count = max(0, j_end - j_start + 1)
    out = np.full(count, np.nan, dtype=np.float64)
    if count == 0 or t_rel_us.size == 0:
        return out
    tk = _grid_times(j_start, j_end, n)
    idx = np.searchsorted(t_rel_us, tk, side="right") - 1
    valid = idx >= 0
    out[valid] = values[idx[valid]]
    if max_gap_us is not None:
        out[_hole_mask(t_rel_us, tk, max_gap_us)] = np.nan
    return out


def linear_grid(
    t_rel_us: np.ndarray,
    values: np.ndarray,
    n: int,
    j_start: int,
    j_end: int,
    max_gap_us: int | None,
) -> np.ndarray:
    """Linear mode: interpolation between the two adjacent samples, NaN outside [t_first, t_last]."""
    count = max(0, j_end - j_start + 1)
    if count == 0 or t_rel_us.size == 0:
        return np.full(count, np.nan, dtype=np.float64)
    tk = _grid_times(j_start, j_end, n)
    out = np.interp(tk, t_rel_us, values, left=np.nan, right=np.nan)
    if max_gap_us is not None:
        out[_hole_mask(t_rel_us, tk, max_gap_us)] = np.nan
    return out


def average_grid(
    t_rel_us: np.ndarray,
    values: np.ndarray,
    n: int,
    j_start: int,
    j_end: int,
    max_gap_us: int | None,
) -> np.ndarray:
    """Average mode: mean of the native samples in [t_k - nΔ/2, t_k + nΔ/2); empty window -> NaN.

    Uses prefix sums (O(samples + points)); NaN samples are ignored. Not available for event channels.
    """
    count = max(0, j_end - j_start + 1)
    out = np.full(count, np.nan, dtype=np.float64)
    if count == 0 or t_rel_us.size == 0:
        return out
    tk = _grid_times(j_start, j_end, n)
    half = (n * DELTA_US) // 2
    lo = np.searchsorted(t_rel_us, tk - half, side="left")
    hi = np.searchsorted(t_rel_us, tk + half, side="left")
    ok = ~np.isnan(values)
    centre = float(values[ok].mean()) if ok.any() else 0.0  # reduces cancellation in the prefix sums
    csum = np.concatenate(([0.0], np.cumsum(np.where(ok, values - centre, 0.0))))
    ccount = np.concatenate(([0], np.cumsum(ok)))
    cnt = ccount[hi] - ccount[lo]
    has = cnt > 0
    out[has] = (csum[hi] - csum[lo])[has] / cnt[has] + centre
    if max_gap_us is not None:
        out[_hole_mask(t_rel_us, tk, max_gap_us)] = np.nan
    return out


MODES = {"step": step_grid, "linear": linear_grid, "average": average_grid}
