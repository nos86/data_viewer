"""Chunked, resumable uploads (RI-05, RF-37). State lives in BlobStorage, not in memory: a restarted backend resumes too.

Keys: uploads/partial/<uid>/meta.json, uploads/partial/<uid>/<index>.part; finished: uploads/<id>.bin + uploads/<id>.json
where <id> is the content hash, i.e. the measurement id (an identical file is never stored or converted twice).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath

from viewer.service.context import Services

MB = 1024 * 1024


class UploadError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.extra = extra


def _meta_key(uid: str) -> str:
    return f"uploads/partial/{uid}/meta.json"


def _part_key(uid: str, i: int) -> str:
    return f"uploads/partial/{uid}/{i:08d}.part"


def upload_id(filename: str, size: int, fingerprint: str) -> str:
    return hashlib.sha256(f"{filename}|{size}|{fingerprint}".encode()).hexdigest()[:24]


def safe_name(filename: str) -> str:
    name = PureWindowsPath(PurePosixPath(filename).name).name.strip()
    name = re.sub(r"[\x00-\x1f]", "", name)
    if not name:
        raise UploadError(422, "The file has no name")
    return name[:200]


def _meta(svc: Services, uid: str) -> dict:
    m = svc.get_json(_meta_key(uid))
    if m is None:
        raise UploadError(404, "Unknown upload (it may have been cancelled or completed)")
    return m


def received(svc: Services, uid: str) -> list[int]:
    prefix = f"uploads/partial/{uid}/"
    out = []
    for k in svc.blobs.list(prefix):
        if k.endswith(".part"):
            out.append(int(k[len(prefix) : -len(".part")]))
    return sorted(out)


def _view(svc: Services, uid: str, m: dict) -> dict:
    return dict(
        upload_id=uid, filename=m["filename"], size=m["size"], chunk_size=m["chunk_size"], total_chunks=m["total_chunks"], received=received(svc, uid)
    )


def create(svc: Services, filename: str, size: int, fingerprint: str) -> dict:
    """Starts an upload, or returns the existing one with the blocks already received (resume)."""
    name = safe_name(filename)
    limit = int(svc.settings.max_upload_mb * MB)
    if size <= 0:
        raise UploadError(422, "The file is empty")
    if size > limit:
        raise UploadError(413, f"The file is {size / MB:,.0f} MB; the largest accepted upload is {svc.settings.max_upload_mb:,} MB")
    uid = upload_id(name, size, fingerprint)
    m = svc.get_json(_meta_key(uid))
    if m is None:
        chunk = max(1, int(svc.settings.upload_chunk_mb * MB))
        m = dict(filename=name, size=size, chunk_size=chunk, total_chunks=-(-size // chunk), created=time.time())
        svc.put_json(_meta_key(uid), m)
    return _view(svc, uid, m)


def put_chunk(svc: Services, uid: str, index: int, data: bytes, sha256: str | None = None) -> dict:
    m = _meta(svc, uid)
    if not 0 <= index < m["total_chunks"]:
        raise UploadError(422, f"Block {index} is outside this upload (0..{m['total_chunks'] - 1})")
    expected = m["chunk_size"] if index < m["total_chunks"] - 1 else m["size"] - m["chunk_size"] * (m["total_chunks"] - 1)
    if len(data) != expected:
        raise UploadError(422, f"Block {index} has {len(data):,} bytes, expected {expected:,}")
    if sha256 and hashlib.sha256(data).hexdigest() != sha256.lower():
        raise UploadError(422, f"Block {index} was corrupted in transit (checksum mismatch): send it again")
    svc.blobs.write(_part_key(uid, index), data)  # idempotent: the same block can be sent again
    return {"index": index, "received": len(received(svc, uid)), "total_chunks": m["total_chunks"]}


def status(svc: Services, uid: str) -> dict:
    return _view(svc, uid, _meta(svc, uid))


def cancel(svc: Services, uid: str) -> None:
    _drop(svc, uid)


def _drop(svc: Services, uid: str) -> None:
    for k in svc.blobs.list(f"uploads/partial/{uid}/"):
        svc.blobs.delete(k)


def complete(svc: Services, uid: str) -> dict:
    """Assembles the blocks. The content hash becomes the measurement id; an identical file already present is reused."""
    m = _meta(svc, uid)
    have = set(received(svc, uid))
    missing = [i for i in range(m["total_chunks"]) if i not in have]
    if missing:
        raise UploadError(409, f"{len(missing)} of {m['total_chunks']} blocks are missing", missing=missing[:200])
    tmp = f"uploads/partial/{uid}/assembled.bin"
    digest = svc.blobs.compose(tmp, [_part_key(uid, i) for i in range(m["total_chunks"])])
    if svc.blobs.size(tmp) != m["size"]:
        svc.blobs.delete(tmp)
        raise UploadError(422, "The assembled file does not have the announced size: upload it again")
    mid = digest[:32]
    existing = svc.catalog.get(mid) is not None
    if existing:
        name = svc.catalog.get(mid).name  # type: ignore[union-attr]
        svc.blobs.delete(tmp)
    else:
        name = m["filename"]
        svc.blobs.move(tmp, f"uploads/{mid}.bin")
        svc.put_json(f"uploads/{mid}.json", dict(name=name, size=m["size"], uploaded_at=datetime.now(timezone.utc).isoformat()))
    _drop(svc, uid)
    return dict(id=mid, name=name, size=m["size"], existing=existing)
