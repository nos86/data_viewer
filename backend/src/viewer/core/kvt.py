"""Shape of the two Delta tables the measurements come from (RI-05) and the selector filters (RF-01).

One place defines columns, the filter semantics and the SQL, so that the local adapters (Delta folders) and the Databricks adapters
(SQL warehouse) answer the same questions the same way.

metadata: uuid, path, vin, configurationId, startOfSampling, endOfSampling   (one row per measurement; times local, naive)
KVT:      uuid, key, value, timestamp                                        (key = channel; rows in acquisition order per channel)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from viewer.core.models import MeasurementInfo, SourceData

META_COLUMNS = ("uuid", "path", "vin", "configurationId", "startOfSampling", "endOfSampling")
KVT_COLUMNS = ("uuid", "key", "value", "timestamp")


@dataclass(frozen=True)
class Filters:
    """Selector of RF-01. Dates are naive local datetimes, compared with startOfSampling (inclusive both ends)."""

    vin: str | None = None
    configuration_id: str | None = None
    start_from: datetime | None = None
    start_to: datetime | None = None

    def any(self) -> bool:
        return any(v is not None for v in (self.vin, self.configuration_id, self.start_from, self.start_to))


@dataclass(frozen=True)
class MetaRow:
    uuid: str
    path: str
    vin: str | None
    configuration_id: str | None
    start: datetime
    end: datetime

    def matches(self, f: Filters | None) -> bool:
        if f is None:
            return True
        if f.vin is not None and (self.vin or "").lower() != f.vin.lower():
            return False
        if f.configuration_id is not None and (self.configuration_id or "").lower() != f.configuration_id.lower():
            return False
        if f.start_from is not None and self.start < f.start_from:
            return False
        if f.start_to is not None and self.start > f.start_to:
            return False
        return True


def meta_from_row(r: dict[str, Any]) -> MetaRow:
    def ts(v: Any) -> datetime:
        return pd.Timestamp(v).to_pydatetime().replace(tzinfo=None)

    return MetaRow(str(r["uuid"]), str(r.get("path") or ""), r.get("vin"), r.get("configurationId"), ts(r["startOfSampling"]), ts(r["endOfSampling"]))


def where_clause(f: Filters | None) -> tuple[str, list[Any]]:
    """`WHERE ...` and its positional parameters for the metadata table (parameter markers: `?`)."""
    parts: list[str] = []
    params: list[Any] = []
    if f:
        if f.vin is not None:
            parts.append("lower(vin) = lower(?)")
            params.append(f.vin)
        if f.configuration_id is not None:
            parts.append("lower(configurationId) = lower(?)")
            params.append(f.configuration_id)
        if f.start_from is not None:
            parts.append("startOfSampling >= ?")
            params.append(f.start_from)
        if f.start_to is not None:
            parts.append("startOfSampling <= ?")
            params.append(f.start_to)
    return ("WHERE " + " AND ".join(parts) if parts else ""), params


def sql_list(table: str, f: Filters | None) -> tuple[str, list[Any]]:
    w, p = where_clause(f)
    return f"SELECT {', '.join(META_COLUMNS)} FROM {table} {w} ORDER BY startOfSampling DESC".replace("  ", " "), p


def sql_get(table: str) -> str:
    return f"SELECT {', '.join(META_COLUMNS)} FROM {table} WHERE uuid = ?"


def sql_kvt(table: str) -> str:
    # no ORDER BY: rows keep the acquisition order, which the fall-back hour inference needs (a sort on local time would mix the two passes)
    return f"SELECT key, value, timestamp FROM {table} WHERE uuid = ?"


def kvt_to_frame(table: Any) -> pd.DataFrame:
    """Arrow table (key, value, timestamp) -> the extractor-shaped frame (timestamp, channel, value) (RI-04)."""
    df = table.to_pandas()
    df = df.rename(columns={"key": "channel"})[["timestamp", "channel", "value"]]
    df["timestamp"] = pd.to_datetime(df["timestamp"]).astype("datetime64[us]")
    if df["value"].dtype == object or str(df["value"].dtype).startswith(("str", "string")):
        df["value"] = parse_values(df["channel"], df["value"])
    return df


def parse_values(channel: pd.Series, value: pd.Series) -> pd.Series:
    """KVT values may be strings: channels whose values all parse become numbers (true/false -> 1/0), the others stay text (and are
    excluded and reported at ingestion, RI-08)."""
    out = value.astype(object).copy()
    codes, names = pd.factorize(channel)
    for i in range(len(names)):
        idx = np.flatnonzero(codes == i)
        raw = value.iloc[idx]
        num = pd.to_numeric(raw, errors="coerce")
        if num.notna().all() or raw.isna().equals(num.isna()):
            out.iloc[idx] = num.to_numpy()
            continue
        low = raw.astype(str).str.strip().str.lower()
        if low.isin(["true", "false"]).all():
            out.iloc[idx] = (low == "true").astype(float).to_numpy()
    num_all = pd.to_numeric(out, errors="coerce")
    return num_all.astype(float) if num_all.notna().all() else out


def info_of(m: MetaRow) -> MeasurementInfo:
    return MeasurementInfo(
        id=m.uuid, name=(m.path.replace("\\", "/").rsplit("/", 1)[-1] or m.uuid), path=m.path, size_bytes=None, vin=m.vin,
        configuration_id=m.configuration_id, start=m.start.isoformat(), end=m.end.isoformat(),
    )


def info_matches(m: MeasurementInfo, f: Filters | None) -> bool:
    """The selector applied to catalogue entries (used by the in-memory catalogue and as a reference for the SQL)."""
    if f is None or not f.any():
        return True
    if m.start is None:
        return False  # no metadata: cannot satisfy a filter
    return MetaRow(m.id, m.path, m.vin, m.configuration_id, datetime.fromisoformat(m.start), datetime.fromisoformat(m.end or m.start)).matches(f)


def source_data(m: MetaRow, table: Any) -> SourceData:
    """The extractor-shaped result for a KVT measurement. `start`/`end` carry startOfSampling/endOfSampling (RI-05)."""
    frame = kvt_to_frame(table)
    return SourceData(
        frame=frame,
        meta={
            "misura": m.path, "path": m.path, "vin": m.vin, "configurationId": m.configuration_id, "datetime": m.start.isoformat(),
            "start": m.start.isoformat(), "end": m.end.isoformat(), "signals": sorted(frame["channel"].unique().tolist()),
        },
    )
