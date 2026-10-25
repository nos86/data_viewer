import numpy as np
import pandas as pd
import pytest

from viewer.adapters.memory import MemoryBlob, MemoryCatalog, MemorySeriesStore, MemorySource
from viewer.config import Settings
from viewer.core.models import MeasurementInfo, SourceData
from viewer.service.cache import SeriesCache
from viewer.service.results import ResultStore
from viewer.service.context import Services


def make_services(frame: pd.DataFrame | None = None, meta: dict | None = None, mid: str = "m1", **settings) -> Services:
    """Services over the in-memory adapters, optionally holding one measurement."""
    catalog, source = MemoryCatalog(), MemorySource()
    if frame is not None:
        catalog.add(MeasurementInfo(mid, f"{mid}.bin", f"mem://{mid}"))
        source.add(mid, SourceData(frame, meta or {"signals": sorted(frame["channel"].unique())}))
    return Services(Settings(**{"slice_rows": 1000, **settings}), catalog, source, MemoryBlob(), MemorySeriesStore(),
                    cache=SeriesCache(64 * 2**20), results=ResultStore(64 * 2**20))


def make_frame(rows: list[tuple]) -> pd.DataFrame:
    """rows: (iso local timestamp | datetime64, channel, value)."""
    df = pd.DataFrame(rows, columns=["timestamp", "channel", "value"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601").astype("datetime64[us]")
    return df


@pytest.fixture
def regular_frame():
    """10 Hz channel 'a' and 1 Hz channel 'b', 60 s, local naive time (no DST)."""
    t = pd.date_range("2026-05-12 10:00:00", periods=600, freq="100ms")
    a = pd.DataFrame({"timestamp": t, "channel": "a", "value": np.arange(600, dtype=float)})
    tb = pd.date_range("2026-05-12 10:00:00", periods=60, freq="1s")
    b = pd.DataFrame({"timestamp": tb, "channel": "b", "value": np.arange(60, dtype=float)})
    df = pd.concat([a, b]).sort_values("timestamp", kind="stable").reset_index(drop=True)
    df["timestamp"] = df["timestamp"].astype("datetime64[us]")
    return df
