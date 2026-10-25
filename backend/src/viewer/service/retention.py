"""Space limit for uploaded binaries and the Parquet cache, least recently used first (RI-39).

A measurement counts: its Parquet, its uploaded binary (if it was uploaded) and its small status/metadata blobs. Source-folder binaries are
never touched (the Parquet of such a file may be evicted and is rebuilt on the next open). A measurement used within `pin_ttl_s` is never
evicted: every API access records the use, and browsers with the measurement open send a heartbeat (`POST /sessions/pin`). Everything is
kept in BlobStorage (`usage/<id>.json`), so the ingestion process, the API and a restarted backend all agree.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from viewer.service.context import Services

GIB = 1024**3
THROTTLE_S = 30.0

_now = time.time  # replaced in tests


def _usage_key(mid: str) -> str:
    return f"usage/{mid}.json"


def touch(svc: Services, mid: str, force: bool = False) -> None:
    """Records that a measurement is in use (throttled: this runs on every request)."""
    t = _now()
    if not force and t - svc.touched.get(mid, 0.0) < THROTTLE_S:
        return
    svc.touched[mid] = t
    svc.put_json(_usage_key(mid), {"last_used": t})


def last_used(svc: Services, mid: str) -> float:
    rec = svc.get_json(_usage_key(mid))
    return max(float(rec["last_used"]) if rec else 0.0, svc.touched.get(mid, 0.0))


@dataclass
class Entry:
    id: str
    series_bytes: int
    upload_bytes: int
    other_bytes: int
    last_used: float

    @property
    def total(self) -> int:
        return self.series_bytes + self.upload_bytes + self.other_bytes


def _small_keys(mid: str) -> list[str]:
    return [f"status/{mid}.json", f"meta/{mid}.json", f"channels/{mid}.json", _usage_key(mid)]


def _upload_ids(svc: Services) -> set[str]:
    return {k[len("uploads/") : -len(".bin")] for k in svc.blobs.list("uploads/") if k.endswith(".bin") and k.count("/") == 1}


def entries(svc: Services) -> list[Entry]:
    uploaded = _upload_ids(svc)
    out = []
    for mid in sorted(set(svc.series.ids()) | uploaded):
        other = sum(svc.blobs.size(k) for k in _small_keys(mid) if svc.blobs.exists(k))
        if mid in uploaded:
            other += svc.blobs.size(f"uploads/{mid}.json") if svc.blobs.exists(f"uploads/{mid}.json") else 0
        out.append(
            Entry(mid, svc.series.size(mid), svc.blobs.size(f"uploads/{mid}.bin") if mid in uploaded else 0, other, last_used(svc, mid))
        )
    return out


def usage(svc: Services) -> dict:
    es = entries(svc)
    limit = int(svc.settings.storage_limit_gb * GIB)
    return dict(
        used_bytes=sum(e.total for e in es),
        limit_bytes=limit,
        uploads_bytes=sum(e.upload_bytes for e in es),
        parquet_bytes=sum(e.series_bytes for e in es),
        measurements=len(es),
    )


def evict(svc: Services, mid: str) -> None:
    svc.series.delete(mid)
    for k in (f"uploads/{mid}.bin", f"uploads/{mid}.json", *_small_keys(mid)):
        svc.blobs.delete(k)
    svc.touched.pop(mid, None)
    if svc.cache is not None:
        svc.cache.discard(lambda key: isinstance(key, tuple) and key and key[0] == mid)


def enforce(svc: Services) -> list[str]:
    """Deletes least-recently-used measurements until the limit is met. Returns the evicted ids (oldest first)."""
    limit = int(svc.settings.storage_limit_gb * GIB)
    if limit <= 0:
        return []
    es = entries(svc)
    total = sum(e.total for e in es)
    evicted: list[str] = []
    horizon = _now() - svc.settings.pin_ttl_s
    for e in sorted(es, key=lambda e: (e.last_used, -e.total)):
        if total <= limit:
            break
        if e.last_used > horizon:
            continue  # in use: never evicted, even if the limit stays exceeded
        evict(svc, e.id)
        total -= e.total
        evicted.append(e.id)
    return evicted
