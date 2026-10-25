"""Frame-level conversion helpers used by the ingestion wrapper (RI-08, RI-09)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from viewer.core.models import IngestError

_NUMERIC_KINDS = {"boolean", "integer", "floating", "mixed-integer-float", "decimal", "empty"}


def to_float(values: np.ndarray) -> np.ndarray | None:
    """float64 values (bool -> 0/1), or None if the channel is not numeric (RI-08)."""
    if values.dtype.kind in "fiub":
        return values.astype(np.float64, copy=False)
    if infer := pd.api.types.infer_dtype(values, skipna=True):
        if infer not in _NUMERIC_KINDS:
            return None
    try:
        return pd.to_numeric(pd.Series(values), errors="raise").to_numpy(dtype=np.float64, na_value=np.nan)
    except (ValueError, TypeError):
        return None


def kind_label(values: np.ndarray) -> str:
    return pd.api.types.infer_dtype(values, skipna=True) if values.dtype == object else str(values.dtype)


def local_to_utc_us(ts_local: np.ndarray, tz: str, channel: str) -> np.ndarray:
    """Naive local datetime64 (sample order) -> int64 µs UTC.

    Ambiguous hours of the fall-back are resolved per channel from the sample order (pandas
    ``ambiguous='infer'``). Consecutive identical stamps are collapsed for the inference, because
    pandas cannot infer through repeated stamps, and expanded back afterwards. A failure to infer, or a
    non-existent local time, aborts the conversion with an explicit error.
    """
    if ts_local.size == 0:
        return np.empty(0, dtype=np.int64)
    ts_local = ts_local.astype("datetime64[us]", copy=False)
    if np.isnat(ts_local).any():
        raise IngestError(f"channel {channel!r}: missing (NaT) timestamps", channel=channel)
    keep = np.empty(ts_local.size, dtype=bool)
    keep[0] = True
    np.not_equal(ts_local[1:], ts_local[:-1], out=keep[1:])
    uniq = ts_local[keep]
    try:
        idx = pd.DatetimeIndex(uniq).tz_localize(tz, ambiguous="infer", nonexistent="raise")
    except (ValueError, OverflowError) as e:  # pytz/zoneinfo ambiguity errors derive from ValueError
        raise IngestError(f"channel {channel!r}: cannot convert local time to UTC ({tz}): {e}", channel=channel) from e
    utc = idx.tz_convert("UTC").as_unit("us").asi8
    return utc if keep.all() else utc[np.cumsum(keep) - 1]


def instant_us(iso_local: str, tz: str, near_us: int) -> int:
    """UTC µs of a metadata time given in local time (startOfSampling / endOfSampling, RI-05).

    A time inside the repeated hour of the fall-back is read as whichever of the two instants is closest to `near_us` (a sample of the
    same measurement), so the metadata never contradicts the data; a time skipped by the spring-forward moves to the first valid one."""
    ts = pd.Timestamp(iso_local).tz_localize(None).as_unit("us")
    try:
        return int(ts.tz_localize(tz, ambiguous="raise", nonexistent="shift_forward").tz_convert("UTC").value // 1000)
    except ValueError:
        both = [int(ts.tz_localize(tz, ambiguous=a).tz_convert("UTC").value // 1000) for a in (True, False)]
        return min(both, key=lambda c: abs(c - near_us))


def sort_dedupe_last(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sort by t (stable) and keep the last delivered value of each duplicated timestamp (RI-08)."""
    order = np.argsort(t, kind="stable")
    t, v = t[order], v[order]
    last = np.empty(t.size, dtype=bool)
    if t.size:
        last[-1] = True
        np.not_equal(t[1:], t[:-1], out=last[:-1])
    return t[last], v[last]
