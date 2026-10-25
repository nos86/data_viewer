"""Binary -> Parquet conversion (RI-04 ... RI-10), run in a separate process.

The wrapper receives the whole extractor DataFrame, turns ``channel`` into a categorical, converts each
channel (local time -> UTC µs, bool -> 0/1, duplicates -> last wins), writes it in slices to the
SeriesStore (row groups of a temporary Parquet) and releases the DataFrame; the store then sorts.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import time
import traceback

import numpy as np
import psutil

from viewer.config import Settings
from viewer.core.grid import assign_n
from viewer.core.models import Block, ChannelInfo, IngestError, IngestStatus
from viewer.service import retention
from viewer.service.context import Services, channels_key, meta_key, status_key
from viewer.service.convert import instant_us, kind_label, local_to_utc_us, sort_dedupe_last, to_float

log = logging.getLogger("viewer.ingest")
from viewer.logging_config import bind, configure_logging


class _Reporter:
    def __init__(self, svc: Services, status: IngestStatus):
        self.svc, self.status = svc, status
        self._phase_start = time.perf_counter()
        self._t_begin = self._phase_start

    def update(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self.status, k, v)
        self.svc.put_json(status_key(self.status.id), self.status.to_dict())

    def phase(self, name: str, progress: float | None = None) -> None:
        now = time.perf_counter()
        if self.status.phase:
            self.status.timings[self.status.phase] = round(now - self._phase_start, 3)
        self._phase_start = now
        self.update(phase=name, **({} if progress is None else {"progress": progress}))

    def finish(self, **kw) -> None:
        now = time.perf_counter()
        self.status.timings[self.status.phase] = round(now - self._phase_start, 3)
        self.status.timings["total"] = round(now - self._t_begin, 3)
        self.update(**kw)


def new_status(mid: str) -> IngestStatus:
    return IngestStatus(id=mid, state="queued", phase="queued", started_at=time.time())


def run_ingest(svc: Services, mid: str) -> IngestStatus:
    """Convert one measurement to Parquet; never raises, the outcome is in the status file."""
    status = svc.get_json(status_key(mid))
    status = IngestStatus.from_dict(status) if status else new_status(mid)
    status.state, status.pid, status.phase, status.progress = "running", os.getpid(), "", 0.0
    rep = _Reporter(svc, status)
    info = svc.catalog.get(mid)
    label = info.name if info else mid
    writer = None
    try:
        rep.phase("extracting", 0.0)
        data = svc.source.read(mid)
        frame, meta = data.frame, data.meta
        svc.put_json(meta_key(mid), {k: meta.get(k) for k in ("misura", "path", "vin", "datetime", "signals")})
        writer = svc.series.begin_write(mid)
        stats = _convert_and_write(svc.settings, frame, writer, rep, label)
        del frame, data
        rep.phase("sorting", 0.9)
        writer.commit()
        writer = None
        rep.phase("cataloguing", 0.97)
        t0 = min(s["t_min"] for s in stats) if stats else 0
        last = max(s["t_max"] for s in stats) if stats else 0
        # KVT measurements: t0 = startOfSampling and end = endOfSampling (RI-05); binaries keep first / last timestamp
        if meta.get("start") and meta.get("end") and stats:
            t_start = instant_us(meta["start"], svc.settings.source_tz, t0)
            t_end = instant_us(meta["end"], svc.settings.source_tz, last)
            if t_start <= t0:
                t0 = t_start
            else:
                status.warnings.append("startOfSampling is later than the first sample: the first sample is used as the start")
            last = max(last, t_end)
        end = last - t0
        channels = [_channel_info(s, t0, svc.settings) for s in stats]
        svc.put_json(channels_key(mid), [c.to_dict() for c in channels])
        rep.finish(state="done", progress=1.0, t0_us=int(t0), end_us=int(end), message=None)
        log.info("ingestion done", extra=dict(file=label, measurement=mid, channels=len(channels), seconds=status.timings.get("total"), warnings=len(status.warnings)))
        retention.touch(svc, mid, force=True)
        retention.enforce(svc)  # the cache may now exceed the limit (RI-39)
    except IngestError as e:
        # file, channel/block and cause as separate fields (RI-44)
        log.error("ingestion failed", extra=dict(file=label, channel=e.channel, block=e.block or status.phase, cause=str(e.__cause__ or e), measurement=mid))
        if writer:
            writer.abort()
        rep.finish(state="error", message=str(e))
    except Exception as e:  # unexpected: keep file, phase and cause (RI-44)
        log.error("ingestion crashed", extra=dict(file=label, block=status.phase, cause=repr(e), measurement=mid), exc_info=True)
        if writer:
            writer.abort()
        rep.finish(state="error", message=f"{label}: unexpected error in phase {status.phase!r}: {e!r}")
    return status


def _convert_and_write(settings: Settings, frame, writer, rep: _Reporter, label: str) -> list[dict]:
    missing = {"timestamp", "channel", "value"} - set(frame.columns)
    if missing:
        raise IngestError(f"{label}: extractor output lacks columns {sorted(missing)}")
    rep.phase("converting", 0.05)
    cat = frame["channel"].astype("category")
    codes = cat.cat.codes.to_numpy()
    names = [str(c) for c in cat.cat.categories]
    ts = frame["timestamp"].to_numpy()
    if ts.dtype.kind != "M":
        raise IngestError(f"{label}: 'timestamp' is not a naive datetime column (dtype {ts.dtype})")
    ts = ts.astype("datetime64[us]", copy=False)
    values = frame["value"].to_numpy()
    del cat
    del frame["channel"]

    order = np.argsort(codes, kind="stable")  # groups the samples of each channel, keeping sample order
    bounds = np.concatenate([[0], np.cumsum(np.bincount(codes, minlength=len(names)))])
    del codes

    stats: list[dict] = []
    for i, name in enumerate(names):
        a, b = int(bounds[i]), int(bounds[i + 1])
        if a == b:
            continue
        sel = order[a:b]
        v = to_float(values[sel])
        if v is None:
            rep.status.warnings.append(
                f"Channel '{name}' excluded: non-numeric values ({kind_label(values[sel])})"
            )
            continue
        try:
            t = local_to_utc_us(ts[sel], settings.source_tz, name)
        except IngestError as e:
            raise IngestError(f"{label}: {e}", file=label, channel=e.channel, block="converting") from e
        t, v = sort_dedupe_last(t, v)
        stats.append(_stats(name, t, v, settings))
        for s in range(0, t.size, settings.slice_rows):
            writer.write_block(Block(name, t[s : s + settings.slice_rows], v[s : s + settings.slice_rows]))
        rep.update(progress=0.05 + 0.85 * (i + 1) / len(names))
    if not stats:
        raise IngestError(f"{label}: no numeric channels in the measurement")
    return stats


def _stats(name: str, t: np.ndarray, v: np.ndarray, settings: Settings) -> dict:
    dt = np.diff(t)
    if dt.size:
        period = int(np.median(dt))
        regular = float(np.mean(np.abs(dt - period) <= 0.3 * period)) if period > 0 else 0.0
        event = regular < settings.event_regular_fraction
    else:
        period, event = None, True
    finite = v[np.isfinite(v)]
    return dict(
        name=name,
        samples=int(t.size),
        t_min=int(t[0]),
        t_max=int(t[-1]),
        period=period,
        event=bool(event),
        v_min=float(finite.min()) if finite.size else None,
        v_max=float(finite.max()) if finite.size else None,
    )


def _channel_info(s: dict, t0: int, settings: Settings) -> ChannelInfo:
    return ChannelInfo(
        name=s["name"],
        samples=s["samples"],
        t_min_us=s["t_min"] - t0,
        t_max_us=s["t_max"] - t0,
        period_us=s["period"],
        event=s["event"],
        v_min=s["v_min"],
        v_max=s["v_max"],
        n=assign_n(s["period"], s["event"]),
        suggested_mode="step" if s["event"] else "linear",
    )


# --- process management ---------------------------------------------------------------------------


def ingest_entry(settings_dict: dict, mid: str, request_id: str | None = None, user: str | None = None, token: str | None = None) -> None:
    """Child-process entry point; keeps the id of the request that started it in every log line."""
    from viewer.service.context import build_services

    settings = Settings(**settings_dict)
    configure_logging(settings.log_level, settings.log_format)
    bind(request_id, user)
    svc = build_services(settings)
    run_ingest(svc.for_user(token, user) if token else svc, mid)  # the child reads with the same permissions as the request


_children: list[multiprocessing.Process] = []


def spawn_process(settings: Settings, mid: str, token: str | None = None) -> None:
    ctx = multiprocessing.get_context("spawn")
    _children[:] = [p for p in _children if p.is_alive()]  # reap finished children
    from viewer.logging_config import request_id_var, user_var

    p = ctx.Process(target=ingest_entry, args=(settings.to_dict(), mid, request_id_var.get(), user_var.get(), token), name=f"ingest-{mid[:8]}")
    p.start()
    _children.append(p)


def effective_status(svc: Services, mid: str) -> IngestStatus | None:
    """Status from storage; a run whose process vanished is reported as an error."""
    raw = svc.get_json(status_key(mid))
    if raw is None:
        return None
    st = IngestStatus.from_dict(raw)
    if st.state in ("queued", "running"):
        alive = psutil.pid_exists(st.pid) if st.pid else (time.time() - st.started_at < 30)
        if not alive:
            st.state, st.message = "error", "Conversion process terminated unexpectedly"
    return st
