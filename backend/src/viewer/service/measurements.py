"""Use cases behind the API: listing, ingestion control, channel catalogue, series (RI-01 service layer)."""
from __future__ import annotations

import threading

import numpy as np

from viewer.core.grid import DELTA_US, MODES, j_max
from viewer.core.models import IngestStatus
from viewer.service import retention
from viewer.service.context import Services, channels_key, meta_key, status_key
from viewer.service.ingest import effective_status, new_status

_start_lock = threading.Lock()


class NotFound(Exception):
    pass


class NotReady(Exception):
    pass


class BadRequest(Exception):
    pass


def _require(svc: Services, mid: str):
    info = svc.catalog.get(mid)
    if info is None:
        raise NotFound(f"Unknown measurement {mid}")
    retention.touch(svc, mid)  # RI-39: every use refreshes the least-recently-used order
    return info


LIST_LIMIT = 500   # per-file status lookups make an unbounded list of tens of thousands of measurements slow; the selector narrows it


def list_files(svc: Services, filters=None, limit: int = LIST_LIMIT) -> list[dict]:
    out = []
    for m in svc.catalog.list(filters)[:limit]:   # catalogues return the most recent first
        st = effective_status(svc, m.id)
        meta = svc.get_json(meta_key(m.id)) or {}
        out.append(
            dict(
                id=m.id,
                name=m.name,
                size_bytes=m.size_bytes,
                vin=m.vin or meta.get("vin"),
                configuration_id=m.configuration_id,
                start=m.start,
                end=m.end,
                state=st.state if st else "not_ingested",
            )
        )
    return out


def get_status(svc: Services, mid: str) -> IngestStatus:
    _require(svc, mid)
    return effective_status(svc, mid) or IngestStatus(id=mid, state="not_ingested")


def start_ingest(svc: Services, mid: str) -> IngestStatus:
    _require(svc, mid)
    with _start_lock:
        st = effective_status(svc, mid)
        if st and st.state in ("queued", "running"):
            return st
        if st and st.state == "done" and svc.series.exists(mid):
            return st
        st = new_status(mid)
        svc.put_json(status_key(mid), st.to_dict())
        if svc.spawn_ingest is None:
            raise RuntimeError("no ingestion runner configured")
        svc.spawn_ingest(mid, svc.token)
        return st


def get_channels(svc: Services, mid: str) -> tuple[bool, list[dict]]:
    """(complete, channels). Before the conversion ends only the extractor's signal names are known."""
    _require(svc, mid)
    full = svc.get_json(channels_key(mid))
    if full is not None:
        return True, full
    meta = svc.get_json(meta_key(mid))
    names = (meta or {}).get("signals") or []
    return False, [dict(name=str(n)) for n in names]


def modes_allowed(channel: dict) -> list[str]:
    """RF-07: average is not available for event channels."""
    return ["step", "linear"] if channel["event"] else ["step", "linear", "average"]


def default_mode(channel: dict) -> str:
    return "step" if channel["event"] else "linear"


def get_series(
    svc: Services,
    mid: str,
    requests: list[tuple[str, str | None]],
    j_start: int | None = None,
    j_end: int | None = None,
) -> dict:
    """requests: (channel, mode or None for the default). Returns step_base_us and series with ndarray values (NaN = null)."""
    _require(svc, mid)
    st = effective_status(svc, mid)
    if not st or st.state != "done":
        raise NotReady("Measurement is not converted yet")
    catalogue = {c["name"]: c for c in svc.get_json(channels_key(mid)) or []}
    unknown = [n for n, _ in requests if n not in catalogue]
    if unknown:
        raise BadRequest(f"Unknown channels: {unknown}")
    series = []
    for name, mode in requests:
        c = catalogue[name]
        mode = mode or default_mode(c)
        if mode not in MODES:
            raise BadRequest(f"Unknown mode {mode!r}")
        if mode not in modes_allowed(c):
            raise BadRequest(f"Mode {mode!r} is not available for event channel {name!r}")
        n = c["n"]
        jm = j_max(st.end_us, n)
        a = 0 if j_start is None else max(0, j_start)
        b = jm if j_end is None else min(jm, j_end)
        full = _full_grid(svc, mid, st, c, mode, jm)
        series.append(dict(file=mid, channel=name, n=n, mode=mode, j_start=a, values=full[a : b + 1] if b >= a else full[:0]))
    return dict(step_base_us=DELTA_US, series=series)


def _full_grid(svc: Services, mid: str, st, c: dict, mode: str, jm: int) -> np.ndarray:
    def compute() -> np.ndarray:
        t_abs, v = svc.series.read_channel(mid, c["name"])
        gap = None if c["event"] else int(round(svc.settings.max_gap_factor * c["period_us"]))
        return MODES[mode](t_abs - st.t0_us, v, c["n"], 0, jm, gap)

    if svc.cache is None:
        return compute()
    return svc.cache.get_or_compute((mid, c["name"], c["n"], mode, 0), compute)
