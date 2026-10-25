"""Ingestion and series-latency benchmark on a (by default) 50M-sample mock measurement.

    python backend/benchmarks/bench_ingest.py [--file data/source/mock_large_50M.bin] [--storage data/bench]

Runs the real path: separate ingestion process, local adapters, HTTP API (TestClient) for latency.
Peak memory is the resident set of the ingestion process sampled every 50 ms, per phase. Note that the
'extracting' phase includes the mock generator (stand-in for the real extractor), so peak memory there is
the generator's; the 'converting'/'sorting' peaks are the wrapper's own cost on top of the DataFrame.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import time
from pathlib import Path

import psutil
from fastapi.testclient import TestClient

from viewer.api.app import create_app
from viewer.config import Settings
from viewer.mock.generate import LARGE, write_mock_bin
from viewer.service import measurements as uc
from viewer.service.context import build_services
from viewer.service.ingest import effective_status

P = "/api/v1"


def run_ingest(settings: Settings, mid: str) -> dict:
    svc = build_services(settings)
    t_start = time.perf_counter()
    uc.start_ingest(svc, mid)
    peak: dict[str, float] = {}
    overall = 0.0
    proc = None
    while True:
        st = effective_status(svc, mid)
        if st and st.pid and proc is None:
            try:
                proc = psutil.Process(st.pid)
            except psutil.NoSuchProcess:
                pass
        if proc is not None and st:
            try:
                rss = proc.memory_info().rss / 2**30
                peak[st.phase] = max(peak.get(st.phase, 0.0), rss)
                overall = max(overall, rss)
            except psutil.NoSuchProcess:
                pass
        if st and st.state in ("done", "error"):
            break
        time.sleep(0.05)
    wall = time.perf_counter() - t_start
    return dict(
        state=st.state, message=st.message, warnings=st.warnings, wall_s=round(wall, 2),
        phase_s=st.timings, peak_rss_gib=round(overall, 2),
        peak_rss_by_phase_gib={k: round(v, 2) for k, v in peak.items()},
        ingest_without_extraction_s=round(st.timings.get("total", wall) - st.timings.get("extracting", 0), 2),
        t0_us=st.t0_us, duration_us=st.end_us,
    )


def series_latency(settings: Settings, mid: str, channels: list[str], repeats: int = 7) -> dict:
    out = {}
    with TestClient(create_app(settings)) as c:
        for label, chans in [(ch, [ch]) for ch in channels] + [("+".join(channels), channels)]:
            times, size = [], 0
            for _ in range(repeats):
                t = time.perf_counter()
                r = c.post(f"{P}/files/{mid}/series", json={"channels": chans})
                times.append(time.perf_counter() - t)
                assert r.status_code == 200, r.text
                size = len(r.content)
            out[label] = dict(
                points=sum(len(s["values"]) for s in r.json()["series"]),
                json_mib=round(size / 2**20, 2),
                first_ms=round(times[0] * 1000, 1),
                repeat_median_ms=round(statistics.median(times[1:]) * 1000, 1),
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=Path, default=Path("data/source/mock_large_50M.bin"))
    ap.add_argument("--storage", type=Path, default=Path("data/bench"))
    ap.add_argument("--keep", action="store_true", help="reuse existing storage instead of wiping it")
    a = ap.parse_args()
    if not a.file.exists():
        name, params = next(iter(LARGE.items()))
        print("generating", write_mock_bin(a.file, params))
    if not a.keep and a.storage.exists():
        shutil.rmtree(a.storage)
    settings = Settings(source_dir=a.file.parent, storage_dir=a.storage)
    svc = build_services(settings)
    mid = next(m.id for m in svc.catalog.list() if m.name == a.file.name)

    print("ingesting", a.file, "...")
    ing = run_ingest(settings, mid)
    print(json.dumps(ing, indent=2))
    if ing["state"] != "done":
        raise SystemExit(1)
    chans = {c["name"]: c for c in svc.get_json(f"channels/{mid}.json")}
    samples = sum(c["samples"] for c in chans.values())
    print(f"channels={len(chans)} samples={samples:,}")
    lat = series_latency(settings, mid, ["f10_000", "f100_000", "f1_000"])
    print(json.dumps(lat, indent=2))
    pq = Path(a.storage) / "parquet" / f"{mid}.parquet"
    res = dict(file=a.file.name, samples=samples, channels=len(chans), parquet_mib=round(pq.stat().st_size / 2**20, 1),
               ingest=ing, series=lat, machine=dict(cpus=psutil.cpu_count(), ram_gib=round(psutil.virtual_memory().total / 2**30, 1)))
    out = a.storage / "result.json"
    out.write_text(json.dumps(res, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
