"""Step 7 benchmarks: a 50M-sample measurement read from a local Delta KVT vs from the binary path, the selector over many
measurements, and the cost of request logging.

    python backend/benchmarks/bench_step7.py [--skip-kvt] [--delta data/delta-large] [--storage data/bench7]

The Delta KVT is written once (partitioned by uuid) and kept for later runs. The Databricks warehouse is not part of this benchmark:
no workspace was reachable, so the SQL path is measured only through its local counterpart (same table layout, same ingestion code).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import psutil
from fastapi.testclient import TestClient

from bench_ingest import run_ingest
from viewer.api.app import create_app
from viewer.config import Settings
from viewer.mock.generate import LARGE, write_mock_bin
from viewer.mock.generator import generate_measurement
from viewer.mock.kvt import LARGE as KVT_LARGE
from viewer.mock.kvt import metadata_table, mock_uuid, write_measurement
from viewer.service.context import build_services

P = "/api/v1"


def kvt_vs_binary(delta: Path, storage: Path) -> dict:
    name, params, vin, cfg = KVT_LARGE
    uid = mock_uuid(name)
    out = {}
    if not (delta / "kvt" / f"uuid={uid}").exists():
        t = time.perf_counter()
        frame, _ = generate_measurement({**params, "name": name})
        out["generate_s"] = round(time.perf_counter() - t, 1)
        t = time.perf_counter()
        write_measurement(delta, uid, frame, f"/mock/{name}.bin", vin, cfg)
        out["write_delta_s"] = round(time.perf_counter() - t, 1)
        out["rows"] = len(frame)
        del frame
    out["delta_mib"] = round(sum(f.stat().st_size for f in (delta / "kvt").rglob("*.parquet")) / 2**20, 1)

    shutil.rmtree(storage, ignore_errors=True)
    s_kvt = Settings(source_dir=storage / "none", storage_dir=storage / "kvt", metadata_dir=delta / "metadata", kvt_dir=delta / "kvt")
    print("ingesting from the Delta KVT ...")
    out["kvt"] = run_ingest(s_kvt, uid)

    src = storage / "src"
    write_mock_bin(src / "mock_large_50M.bin", next(iter(LARGE.values())))
    s_bin = Settings(source_dir=src, storage_dir=storage / "bin")
    mid = next(m.id for m in build_services(s_bin).catalog.list())
    print("ingesting from the binary path ...")
    out["binary"] = run_ingest(s_bin, mid)
    for k in ("kvt", "binary"):
        r = out[k]
        r["ingest_without_extraction_s"] = r.get("ingest_without_extraction_s")
    return out


def selector_latency(rows: int = 20_000) -> dict:
    from deltalake import write_deltalake

    base = datetime(2025, 1, 1)
    with tempfile.TemporaryDirectory() as tmp:
        meta = [dict(uuid=mock_uuid(f"m{i}"), path=f"/data/{i}.bin", vin=f"VIN{i % 400:05d}", configurationId=f"CFG-{i % 12}",
                     start=base + timedelta(minutes=17 * i), end=base + timedelta(minutes=17 * i + 15)) for i in range(rows)]
        for a in range(0, rows, 5000):
            write_deltalake(str(Path(tmp) / "metadata"), metadata_table(meta[a:a + 5000]), mode="append")
        write_deltalake(str(Path(tmp) / "kvt"), __import__("pyarrow").table({"uuid": ["x"], "key": ["k"], "value": [1.0], "timestamp": __import__("pyarrow").array([base], __import__("pyarrow").timestamp("us"))}), mode="append")
        s = Settings(source_dir=Path(tmp) / "src", storage_dir=Path(tmp) / "st", metadata_dir=Path(tmp) / "metadata", kvt_dir=Path(tmp) / "kvt")
        res = {}
        with TestClient(create_app(s)) as c:
            for label, q in [("all (500 shown)", ""), ("vin", "?vin=VIN00007"), ("vin+cfg+range", "?vin=VIN00007&configuration_id=CFG-7&start_from=2025-02-01T00:00:00&start_to=2025-06-01T00:00:00")]:
                times = []
                for _ in range(7):
                    t = time.perf_counter()
                    r = c.get(f"{P}/files{q}")
                    times.append((time.perf_counter() - t) * 1000)
                    assert r.status_code == 200
                res[label] = dict(measurements=len(r.json()), first_ms=round(times[0], 1), median_ms=round(statistics.median(times[1:]), 1))
    res["metadata_rows"] = rows
    return res


def logging_overhead(n: int = 3000) -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for label, level, fmt in [("WARNING (no access record)", "WARNING", "text"), ("INFO text", "INFO", "text"), ("INFO json", "INFO", "json")]:
            s = Settings(source_dir=Path(tmp) / "s", storage_dir=Path(tmp) / "d", log_level=level, log_format=fmt)
            with TestClient(create_app(s)) as c:
                for h in logging.getLogger().handlers:
                    if getattr(h, "_viewer", False):
                        h.setStream(open(os.devnull, "w"))   # measure formatting, not the console
                for _ in range(100):
                    c.get(f"{P}/version")
                t = time.perf_counter()
                for _ in range(n):
                    c.get(f"{P}/version")
                out[label] = round((time.perf_counter() - t) / n * 1e6, 1)
    out["unit"] = "µs per request, TestClient round trip"
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", type=Path, default=Path("data/delta-large"))
    ap.add_argument("--storage", type=Path, default=Path("data/bench7"))
    ap.add_argument("--skip-kvt", action="store_true")
    a = ap.parse_args()
    res = dict(machine=dict(cpus=psutil.cpu_count(), ram_gib=round(psutil.virtual_memory().total / 2**30, 1)))
    if not a.skip_kvt:
        res["kvt_vs_binary"] = kvt_vs_binary(a.delta, a.storage)
        print(json.dumps(res["kvt_vs_binary"], indent=2))
    res["selector"] = selector_latency()
    print(json.dumps(res["selector"], indent=2))
    res["logging"] = logging_overhead()
    print(json.dumps(res["logging"], indent=2))
    a.storage.mkdir(parents=True, exist_ok=True)
    (a.storage / "result.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
