"""Upload throughput (chunked, 50 MB blocks) and export of 4 graphs x 10 channels x 108k points, in-process through the API.

    python backend/benchmarks/bench_step6.py [--upload-mb 500] [--storage data/storage]

The export part needs the 50M mock measurement to be converted in that storage (open it once in the app, or run bench_ingest.py).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import tempfile
import time
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from viewer.api.app import create_app
from viewer.config import Settings

P = "/api/v1"
MB = 1024 * 1024


def bench_upload(mb: int) -> dict:
    tmp = Path(tempfile.mkdtemp())
    try:
        settings = Settings(source_dir=tmp / "src", storage_dir=tmp / "store", max_upload_mb=mb * 2, upload_chunk_mb=50.0)
        with TestClient(create_app(settings)) as c:
            block = hashlib.sha256(b"seed").digest() * (MB // 32)       # 1 MB of pseudo-random-looking bytes
            size = mb * MB
            t = time.perf_counter()
            u = c.post(f"{P}/uploads", json={"filename": "big.bin", "size": size, "fingerprint": "bench"}).json()
            h_total = hashlib.sha256()
            send = 0.0
            hashing = 0.0
            for i in range(u["total_chunks"]):
                n = min(u["chunk_size"], size - i * u["chunk_size"])
                data = (block * (n // MB + 1))[:n]
                t1 = time.perf_counter()
                digest = hashlib.sha256(data).hexdigest()
                hashing += time.perf_counter() - t1
                t1 = time.perf_counter()
                r = c.put(f"{P}/uploads/{u['upload_id']}/chunks/{i}", content=data, headers={"X-Chunk-SHA256": digest})
                send += time.perf_counter() - t1
                assert r.status_code == 200, r.text
                h_total.update(data)
            t1 = time.perf_counter()
            done = c.post(f"{P}/uploads/{u['upload_id']}/complete").json()
            finish = time.perf_counter() - t1
            total = time.perf_counter() - t
            assert done["id"] == h_total.hexdigest()[:32]
        return dict(mb=mb, chunks=u["total_chunks"], total_s=round(total, 2), blocks_s=round(send, 2), client_hash_s=round(hashing, 2),
                    assemble_s=round(finish, 2), mb_per_s=round(mb / total, 1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bench_export(storage: Path) -> dict:
    settings = Settings(source_dir=Path("data/source"), storage_dir=storage)
    with TestClient(create_app(settings)) as c:
        files = {f["name"]: f for f in c.get(f"{P}/files").json()}
        f = files["mock_large_50M.bin"]
        if f["state"] != "done":
            return {"skipped": "mock_large_50M.bin is not converted in this storage"}
        series = [{"label": f"A: f10_{i:03d}", "file": f["id"], "channel": f"f10_{i:03d}", "mode": "linear"} for i in range(40)]
        out = {}
        for fmt in ("csv", "parquet"):
            for run in ("cold", "warm"):
                t = time.perf_counter()
                with c.stream("POST", f"{P}/export", json={"format": fmt, "series": series}) as r:
                    first = None
                    n = 0
                    for chunk in r.iter_bytes():
                        if first is None:
                            first = time.perf_counter() - t
                        n += len(chunk)
                total = time.perf_counter() - t
                out[f"{fmt}/{run}"] = dict(first_byte_s=round(first or total, 2), total_s=round(total, 2), mib=round(n / MB, 1))
        rows = len(pd.read_parquet(io.BytesIO(c.post(f"{P}/export", json={"format": "parquet", "series": series}).content)))
        out["rows"] = rows
        return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--upload-mb", type=int, default=500)
    ap.add_argument("--storage", type=Path, default=Path("data/storage"))
    a = ap.parse_args()
    print(json.dumps(dict(upload=bench_upload(a.upload_mb), export=bench_export(a.storage)), indent=2))
