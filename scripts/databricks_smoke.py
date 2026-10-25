"""Checks a Databricks deployment. NOT run by the test suite: it needs a workspace (RI-33…36, RI-40).

    # a deployed app answers and runs the expected version (used by the pipeline); DATABRICKS_TOKEN authenticates the request
    python scripts/databricks_smoke.py --app-url https://viewer-staging-123.azure.databricksapps.com --expect-version 1.4.0

    # the adapters against the real workspace, with the settings of VIEWER_DBX_* and a token (DATABRICKS_TOKEN or VIEWER_DBX_TOKEN)
    python scripts/databricks_smoke.py --adapters [--measurement UUID] [--vin VIN]
      1. lists the measurements the token may see (filtered by VIN when --vin is given)
      2. reads one measurement's KVT through the warehouse (the first listed, or --measurement)
      3. writes, reads, moves and deletes a blob on the Volume
      4. persists, lists and removes a Parquet file through the series store
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request


def check_app(url: str, expect: str | None) -> None:
    token = os.environ.get("DATABRICKS_TOKEN", "")
    req = urllib.request.Request(url.rstrip("/") + "/api/v1/version", headers={"Authorization": f"Bearer {token}"} if token else {})
    for _ in range(30):   # the app takes a moment to start after a deploy
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                got = json.load(r)["version"]
            break
        except Exception as e:
            print(f"  waiting for the app ({e})")
            time.sleep(10)
    else:
        raise SystemExit("the app did not answer /api/v1/version")
    print("app version:", got)
    if expect and got != expect:
        raise SystemExit(f"expected version {expect}, the app runs {got}")


def check_adapters(measurement: str | None, vin: str | None) -> None:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "src"))
    import numpy as np

    from viewer.adapters.databricks import build_databricks
    from viewer.config import Settings
    from viewer.core.kvt import Filters
    from viewer.core.models import Block

    s = Settings.from_env()
    token = os.environ.get("DATABRICKS_TOKEN") or s.dbx_token
    if not token:
        raise SystemExit("set DATABRICKS_TOKEN (or VIEWER_DBX_TOKEN)")
    built = build_databricks(s)
    catalog, source = built["user_factory"](token)
    t = time.time()
    items = catalog.list(Filters(vin=vin) if vin else None)
    print(f"1. {len(items)} measurements listed in {time.time() - t:.1f}s")
    if not items:
        raise SystemExit("nothing to read: the token sees no measurement")
    mid = measurement or items[0].id
    t = time.time()
    data = source.read(mid)
    print(f"2. read {mid}: {len(data.frame):,} rows, {len(data.meta.get('signals', []))} channels in {time.time() - t:.1f}s")
    blobs = built["blobs"]
    key = f"smoke/{int(time.time())}.txt"
    blobs.write(key, b"hello")
    assert blobs.read(key) == b"hello"
    blobs.move(key, key + ".moved")
    assert blobs.exists(key + ".moved") and not blobs.exists(key)
    blobs.delete(key + ".moved")
    print("3. volume blob write/read/move/delete ok")
    series = built["series"]
    w = series.begin_write("smoke-test")
    w.write_block(Block("a", np.array([1, 2, 3], np.int64), np.array([1.0, 2.0, 3.0])))
    w.commit()
    assert "smoke-test" in series.ids() and series.channels("smoke-test") == ["a"]
    series.delete("smoke-test")
    print("4. series store persist/read/delete ok")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app-url")
    ap.add_argument("--expect-version")
    ap.add_argument("--adapters", action="store_true")
    ap.add_argument("--measurement")
    ap.add_argument("--vin")
    a = ap.parse_args()
    if not a.app_url and not a.adapters:
        ap.error("give --app-url and/or --adapters")
    if a.app_url:
        check_app(a.app_url, a.expect_version)
    if a.adapters:
        check_adapters(a.measurement, a.vin)
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
