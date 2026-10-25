"""Writes mock measurements as the two Delta tables of the real system: metadata and KVT (uuid, key, value, timestamp).

    python -m viewer.mock.kvt [--out data/delta] [--large] [--value-type double|string]

Tables are partitioned by uuid, so that one measurement is read without scanning the others.
"""
from __future__ import annotations

import argparse
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa

from viewer.mock.generator import generate_measurement

CHUNK_ROWS = 5_000_000
NAMESPACE = uuid.UUID("6f1d3c52-0c1e-4c5a-9a53-3d1f0a7c9b11")


def mock_uuid(name: str) -> str:
    return str(uuid.uuid5(NAMESPACE, name))


def kvt_table(uid: str, frame: pd.DataFrame, value_type: str) -> pa.Table:
    f = frame
    if value_type == "double":
        keep = f["value"].map(lambda v: not isinstance(v, str)) if f["value"].dtype == object else pd.Series(True, index=f.index)
        f = f[keep]
        values = pa.array(f["value"].astype(float).to_numpy(), pa.float64())
    else:
        values = pa.array(f["value"].map(lambda v: str(v).lower() if isinstance(v, (bool, np.bool_)) else str(v)).tolist(), pa.string())
    return pa.table(
        {
            "uuid": pa.array([uid] * len(f), pa.string()),
            "key": pa.array(f["channel"].astype(str).to_numpy(), pa.string()),
            "value": values,
            "timestamp": pa.array(f["timestamp"].to_numpy().astype("datetime64[us]"), pa.timestamp("us")),
        }
    )


def metadata_table(rows: list[dict]) -> pa.Table:
    return pa.table(
        {
            "uuid": [r["uuid"] for r in rows], "path": [r["path"] for r in rows], "vin": [r["vin"] for r in rows],
            "configurationId": [r["configurationId"] for r in rows],
            "startOfSampling": pa.array([r["start"] for r in rows], pa.timestamp("us")),
            "endOfSampling": pa.array([r["end"] for r in rows], pa.timestamp("us")),
        }
    )


def write_measurement(out: Path, uid: str, frame: pd.DataFrame, path: str, vin: str, configuration_id: str, value_type: str = "double",
                      start: datetime | None = None, end: datetime | None = None) -> None:
    from deltalake import write_deltalake

    out = Path(out)
    for a in range(0, len(frame), CHUNK_ROWS):   # slices keep the Arrow copy small for 50M-row measurements; files stay in time order
        write_deltalake(str(out / "kvt"), kvt_table(uid, frame.iloc[a:a + CHUNK_ROWS], value_type), mode="append", partition_by=["uuid"])
    ts = frame["timestamp"]
    row = dict(uuid=uid, path=path, vin=vin, configurationId=configuration_id,
               start=(start or ts.iloc[0].to_pydatetime()), end=(end or ts.iloc[-1].to_pydatetime()))
    write_deltalake(str(out / "metadata"), metadata_table([row]), mode="append")


SAMPLES = [
    ("kvt_tiny", dict(profile="tiny", seed=1, start="2026-03-02T08:30:00"), "MOCKVIN0000000001", "CFG-A"),
    ("kvt_small", dict(profile="small", seed=2, start="2026-05-12T10:00:00"), "MOCKVIN0000000002", "CFG-B"),
    ("kvt_dst_2026-10-25", dict(profile="tiny", seed=3, dst=True, duration_s=10800.0), "MOCKVIN0000000001", "CFG-A"),
]
LARGE = ("kvt_large_50M", dict(profile="large", seed=4, start="2026-06-01T09:00:00"), "MOCKVIN0000000003", "CFG-B")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("data/delta"))
    ap.add_argument("--large", action="store_true", help="also write the 50M-sample measurement")
    ap.add_argument("--value-type", choices=["double", "string"], default="double")
    a = ap.parse_args(argv)
    for name, params, vin, cfg in [*SAMPLES, *([LARGE] if a.large else [])]:
        frame, _ = generate_measurement({**params, "name": name})
        write_measurement(a.out, mock_uuid(name), frame, f"/mock/{name}.bin", vin, cfg, a.value_type)
        print("wrote", name, mock_uuid(name), f"{len(frame):,} rows")
    print(f"metadata: {a.out / 'metadata'}\nKVT:      {a.out / 'kvt'}\nexport VIEWER_METADATA_DIR={a.out / 'metadata'} VIEWER_KVT_DIR={a.out / 'kvt'}")


if __name__ == "__main__":
    main()
