"""Deterministic synthetic measurement, stand-in for the real extractor output (RI-04).

A fake ``.bin`` is a small JSON file with seed and parameters; ``generate_measurement`` expands it into
the DataFrame (timestamp naive Europe/Rome, channel, value) plus the extractor metadata JSON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

TZ = "Europe/Rome"
DST_START_UTC = datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc)  # 01:30 CEST, before the 25/10/2026 fall-back
DEFAULT_START = "2026-05-12T10:00:00"


@dataclass
class ChannelSpec:
    name: str
    hz: float = 0.0
    kind: str = "float"        # float | bool | str | event
    jitter: float = 0.05       # sd of the timestamp jitter, as a share of the period
    missing: float = 0.0       # probability of a single missing sample
    gaps: list[tuple[float, float]] = field(default_factory=list)  # long interruptions [start_s, end_s)
    dup: float = 0.0           # probability that a sample is delivered twice (second value differs)
    event_mean_s: float = 20.0


def profile_channels(profile: str, duration_s: float, seed: int) -> list[ChannelSpec]:
    rng = np.random.default_rng([seed, 99])

    def gap() -> list[tuple[float, float]]:
        length = float(rng.uniform(0.01, 0.02)) * duration_s
        start = float(rng.uniform(0.1, 0.8)) * duration_s
        return [(start, start + length)]

    specs: list[ChannelSpec] = []

    def add(prefix: str, hz: float, count: int, gap_every: int = 3, **kw):
        for i in range(count):
            specs.append(
                ChannelSpec(f"{prefix}_{i:03d}", hz, gaps=gap() if gap_every and i % gap_every == 0 else [], **kw)
            )

    if profile == "tiny":
        add("f100", 100, 1, missing=0.0005, dup=0.0005)
        add("f10", 10, 1, missing=0.002, dup=0.002)
        add("f5", 5, 1)
        add("f2", 2, 1)
        add("f1", 1, 1)
        add("f3", 3, 1)
        specs.append(ChannelSpec("ev_000", kind="event"))
        specs.append(ChannelSpec("flag_000", 1, kind="bool", gaps=[]))
        specs.append(ChannelSpec("state_000", 1, kind="str", gaps=[]))
    elif profile == "small":  # ~1M samples
        add("f100", 100, 3, missing=0.0005, dup=0.0005)
        add("f10", 10, 10, missing=0.002, dup=0.002)
        add("f5", 5, 5)
        add("f2", 2, 4)
        add("f1", 1, 6)
        add("f3", 3, 3)
        for i in range(3):
            specs.append(ChannelSpec(f"ev_{i:03d}", kind="event"))
        specs.append(ChannelSpec("flag_000", 1, kind="bool"))
        specs.append(ChannelSpec("state_000", 1, kind="str"))
    elif profile == "large":  # ~51M samples over 3 h, float64 only (see docs/benchmarks.md)
        add("f100", 100, 30, missing=0.0002, dup=0.0002)
        add("f10", 10, 120, missing=0.001, dup=0.001)
        add("f5", 5, 40)
        add("f2", 2, 50)
        add("f1", 1, 100)
        add("f3", 3, 50)
        for i in range(30):
            specs.append(ChannelSpec(f"ev_{i:03d}", kind="event"))
    else:
        raise ValueError(f"unknown profile {profile!r}")
    return specs


DEFAULT_DURATION = {"tiny": 120.0, "small": 2400.0, "large": 10800.0}


def _channel_arrays(spec: ChannelSpec, idx: int, duration_s: float, seed: int, first: bool):
    """-> (t_us int64 sample order, values float64 | object)."""
    rng = np.random.default_rng([seed, idx])
    if spec.kind == "event":
        gaps = rng.exponential(spec.event_mean_s, size=int(duration_s / spec.event_mean_s * 2) + 8)
        t = np.cumsum(gaps)
        t = t[t < duration_s]
        t_us = np.round(t * 1e6).astype(np.int64)
        v = np.cumsum(rng.integers(-2, 3, size=t.size)).astype(np.float64)
        return t_us, v
    period = 1.0 / spec.hz
    n = int(duration_s * spec.hz)
    phase = 0.0 if first else float(rng.uniform(0, period))
    t = np.arange(n) * period + phase
    jit = np.clip(rng.normal(0.0, spec.jitter * period, n), -0.3 * period, 0.3 * period)
    if first:
        jit[0] = 0.0
    t = np.maximum(t + jit, 0.0)
    t_us = np.round(t * 1e6).astype(np.int64)
    ts = t_us / 1e6
    amp = 10.0 ** rng.integers(-1, 4)
    freq = rng.uniform(0.005, 0.2)
    if spec.kind == "bool":
        v: np.ndarray = np.sin(2 * np.pi * freq * ts) > 0
    elif spec.kind == "str":
        states = np.array(["IDLE", "RUN", "ERR"], dtype=object)
        v = states[(np.floor(ts / 30).astype(int)) % 3]
    else:
        v = amp * np.sin(2 * np.pi * freq * ts + rng.uniform(0, 6.28)) + amp * rng.normal(0, 0.01, n) + amp
    keep = np.ones(n, dtype=bool)
    if spec.missing:
        keep &= rng.random(n) >= spec.missing
    for a, b in spec.gaps:
        keep &= ~((ts >= a) & (ts < b))
    t_us, v = t_us[keep], v[keep]
    if spec.dup and spec.kind == "float":
        counts = 1 + (rng.random(t_us.size) < spec.dup)
        rep = np.repeat(np.arange(t_us.size), counts)
        second = np.r_[False, rep[1:] == rep[:-1]]
        t_us, v = t_us[rep], v[rep].copy()
        v[second] += 1.0  # the later delivery wins at ingestion
    return t_us, v


def generate_measurement(params: dict) -> tuple[pd.DataFrame, dict]:
    """Expand ``params`` (the content of a fake .bin) into (DataFrame, metadata)."""
    seed = int(params.get("seed", 1))
    profile = params.get("profile", "small")
    dst = bool(params.get("dst", False))
    duration = float(params.get("duration_s", 10800.0 if dst else DEFAULT_DURATION.get(profile, 600.0)))
    if "channels" in params:
        specs = [ChannelSpec(**{**c, "gaps": [tuple(g) for g in c.get("gaps", [])]}) for c in params["channels"]]
    else:
        specs = profile_channels(profile, duration, seed)

    t_parts, code_parts, v_parts = [], [], []
    object_values = any(s.kind in ("bool", "str") for s in specs)
    for i, s in enumerate(specs):
        t_us, v = _channel_arrays(s, i, duration, seed, first=(i == 0))
        t_parts.append(t_us)
        code_parts.append(np.full(t_us.size, i, dtype=np.int32))
        v_parts.append(v.astype(object) if object_values else v)
    t_all = np.concatenate(t_parts)
    codes = np.concatenate(code_parts)
    values = np.concatenate(v_parts)
    del t_parts, code_parts, v_parts
    order = np.argsort(t_all, kind="stable")  # as delivered by a time-ordered acquisition
    t_all, codes, values = t_all[order], codes[order], values[order]
    del order

    if dst:
        start_utc = DST_START_UTC
        utc = (np.datetime64(start_utc.replace(tzinfo=None), "us") + t_all.astype("timedelta64[us]"))
        local = pd.DatetimeIndex(utc).tz_localize("UTC").tz_convert(TZ).tz_localize(None).as_unit("us").to_numpy()
        start_local = local[0]
    else:
        start_local = np.datetime64(params.get("start", DEFAULT_START), "us")
        local = start_local + t_all.astype("timedelta64[us]")
    names = np.array([s.name for s in specs], dtype=object)
    frame = pd.DataFrame(
        {
            "timestamp": local,
            "channel": pd.Series(names[codes], dtype=object),
            "value": pd.Series(values, dtype=object if object_values else np.float64),
        }
    )
    meta = {
        "misura": params.get("name", "mock"),
        "path": params.get("path", "mock://measurement"),
        "vin": params.get("vin", f"MOCKVIN{seed:010d}"),
        # deliberately NOT the start of sampling (RI-05): t0 is the first DataFrame timestamp
        "datetime": (pd.Timestamp(start_local) - timedelta(seconds=5)).isoformat(),
        "signals": [s.name for s in specs],
    }
    return frame, meta
