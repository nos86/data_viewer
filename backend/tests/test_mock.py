import numpy as np
import pandas as pd
import pytest

from viewer.extractor_placeholder import extract
from viewer.mock.generate import SAMPLES, write_mock_bin
from viewer.mock.generator import generate_measurement


def dts(g, ch):
    return np.diff(g.get_group(ch)["timestamp"].to_numpy()).astype("timedelta64[us]").astype(int)


def test_deterministic_and_seed_sensitive():
    p = {"profile": "tiny", "seed": 5}
    a, ma = generate_measurement(p)
    b, mb = generate_measurement(p)
    assert a.equals(b) and ma == mb
    c, _ = generate_measurement({**p, "seed": 6})
    assert not a.equals(c)


def test_contents_of_tiny_profile():
    df, meta = generate_measurement({"profile": "tiny", "seed": 1})
    assert list(df.columns) == ["timestamp", "channel", "value"]
    assert df["timestamp"].dtype.kind == "M" and df["timestamp"].dt.tz is None
    assert set(meta) == {"misura", "path", "vin", "datetime", "signals"}
    assert pd.Timestamp(meta["datetime"]) < df["timestamp"].min()         # not the start of sampling
    g = df.groupby("channel")
    assert {"f100_000", "f10_000", "f5_000", "f2_000", "f1_000", "f3_000", "ev_000", "flag_000", "state_000"} <= set(g.groups)
    for ch, hz in [("f100_000", 100), ("f10_000", 10), ("f5_000", 5), ("f2_000", 2), ("f1_000", 1), ("f3_000", 3)]:
        assert abs(np.median(dts(g, ch)) - 1e6 / hz) < 0.02e6 / hz     # native rate
    assert np.unique(dts(g, "f10_000")).size > 10                       # jitter
    assert all(isinstance(v, (bool, np.bool_)) for v in g.get_group("flag_000")["value"])
    assert set(g.get_group("state_000")["value"]) <= {"IDLE", "RUN", "ERR"}
    assert g.get_group("f100_000")["timestamp"].duplicated().any()      # duplicated timestamps
    assert (dts(g, "f10_000") > 150_000).any()                          # single missing samples
    assert (dts(g, "f100_000") > 1_000_000).any()                       # long interruption


def test_float_only_when_no_non_numeric_channels():
    df, _ = generate_measurement({"seed": 1, "duration_s": 30,
                                  "channels": [{"name": "a", "hz": 10}, {"name": "e", "kind": "event"}]})
    assert df["value"].dtype == np.float64


def test_dst_option_spans_the_repeated_hour():
    df, _ = generate_measurement({"seed": 1, "dst": True, "duration_s": 10800,
                                  "channels": [{"name": "a", "hz": 1}]})
    ts = df["timestamp"].to_numpy()
    assert (np.diff(ts).astype("timedelta64[s]").astype(int) < 0).any()   # wall clock goes back
    assert ts.min() >= np.datetime64("2026-10-25T01:30:00")


def test_placeholder_extract_reads_fake_bin(tmp_path):
    p = write_mock_bin(tmp_path / "x.bin", SAMPLES["mock_tiny.bin"])
    df, meta = extract(str(p))
    assert len(df) > 1000 and meta["path"] == str(p)


def test_placeholder_rejects_garbage(tmp_path):
    p = tmp_path / "bad.bin"
    p.write_bytes(b"\x00\x01\x02garbage")
    with pytest.raises(ValueError, match="not a recognised"):
        extract(str(p))
