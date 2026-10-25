import numpy as np
import pandas as pd
import pytest

from conftest import make_frame, make_services
from viewer.core.models import SourceData
from viewer.service import measurements as uc
from viewer.service.context import channels_key
from viewer.service.ingest import run_ingest

T0 = int(pd.Timestamp("2026-05-12 10:00:00", tz="Europe/Rome").tz_convert("UTC").value // 1000)


def series(svc, names, mode="step", **kw):
    """get_series with list values (None = null), like the JSON the API sends."""
    res = uc.get_series(svc, "m1", [(n, mode) for n in names], **kw)
    for s in res["series"]:
        s["values"] = [None if x != x else float(x) for x in s["values"]]
    return res


def ingest(frame, **settings):
    svc = make_services(frame, **settings)
    st = run_ingest(svc, "m1")
    return svc, st


def test_regular_measurement_catalogue_and_grid(regular_frame):
    svc, st = ingest(regular_frame)
    assert st.state == "done", st.message
    ch = {c["name"]: c for c in svc.get_json(channels_key("m1"))}
    assert ch["a"]["n"] == 1 and ch["b"]["n"] == 10
    assert ch["a"]["samples"] == 600 and ch["a"]["period_us"] == 100_000 and not ch["a"]["event"]
    assert ch["a"]["v_min"] == 0 and ch["a"]["v_max"] == 599
    assert st.t0_us == T0
    assert st.end_us == 59_900_000
    a, b = series(svc, ["a", "b"])["series"]
    assert a["values"] == [float(i) for i in range(600)]          # step on the native grid
    assert b["n"] == 10 and b["values"][:3] == [0.0, 1.0, 2.0]     # one point per second


def test_t0_is_first_timestamp_not_metadata_datetime(regular_frame):
    svc = make_services(regular_frame)
    svc.source.add("m1", SourceData(regular_frame, {"signals": ["a", "b"], "datetime": "2020-01-01T00:00:00"}))
    assert run_ingest(svc, "m1").t0_us == T0


def test_duplicate_timestamps_last_wins():
    rows = [("2026-05-12 10:00:00", "a", 1.0), ("2026-05-12 10:00:00.1", "a", 2.0),
            ("2026-05-12 10:00:00.1", "a", 22.0), ("2026-05-12 10:00:00.2", "a", 3.0)]
    svc, st = ingest(make_frame(rows))
    assert st.state == "done"
    assert series(svc, ["a"])["series"][0]["values"] == [1.0, 22.0, 3.0]


def test_bool_to_01_and_non_numeric_excluded_and_reported():
    rows = []
    for i in range(5):
        ts = f"2026-05-12 10:00:0{i}"
        rows += [(ts, "flag", bool(i % 2)), (ts, "state", "RUN"), (ts, "num", float(i))]
    frame = make_frame(rows)
    frame["value"] = frame["value"].astype(object)
    svc, st = ingest(frame)
    assert st.state == "done"
    assert [c["name"] for c in svc.get_json(channels_key("m1"))] == ["flag", "num"]
    assert any("state" in w and "non-numeric" in w for w in st.warnings)
    flag = series(svc, ["flag"])["series"][0]["values"]
    assert [flag[i] for i in range(5)] == [0.0, 1.0, 0.0, 1.0, 0.0]


def test_event_channel_detected_and_hold_value():
    rows = [(f"2026-05-12 10:00:{s:02d}", "x", float(s)) for s in range(0, 59)]
    for ts, v in [("10:00:03.2", 1.0), ("10:00:17.9", 2.0), ("10:00:21.0", 5.0), ("10:00:48.4", 3.0)]:
        rows.append((f"2026-05-12 {ts}", "ev", v))
    svc, st = ingest(make_frame(rows))
    ch = {c["name"]: c for c in svc.get_json(channels_key("m1"))}
    assert ch["ev"]["event"] and ch["ev"]["n"] == 1 and ch["ev"]["suggested_mode"] == "step"
    assert not ch["x"]["event"]
    v = series(svc, ["ev"])["series"][0]["values"]
    # held until the next event, no gap limit; None before the first event
    assert v[0] is None and v[32] == 1.0 and v[179] == 2.0 and v[210] == 5.0 and v[500] == 3.0


def test_fall_back_measurement_converts_to_utc():
    utc = pd.date_range("2026-10-24T23:30:00Z", "2026-10-25T02:30:00Z", freq="30s")
    local = utc.tz_convert("Europe/Rome").tz_localize(None).as_unit("us")
    frame = pd.DataFrame({"timestamp": local, "channel": "a", "value": np.arange(len(utc), dtype=float)})
    svc, st = ingest(frame)
    assert st.state == "done", st.message
    t, v = svc.series.read_channel("m1", "a")
    assert (t == utc.as_unit("us").asi8).all() and (v == np.arange(len(utc))).all()
    assert (np.diff(t) == 30_000_000).all()


def test_fall_back_unresolvable_channel_aborts_with_explicit_error():
    utc = pd.date_range("2026-10-24T23:30:00Z", "2026-10-25T02:30:00Z", freq="10min")
    local = utc.tz_convert("Europe/Rome").tz_localize(None).as_unit("us")
    good = pd.DataFrame({"timestamp": local, "channel": "good", "value": 1.0})
    bad = make_frame([("2026-10-25 02:30:00", "lonely", 1.0)])      # one sample inside the repeated hour
    svc, st = ingest(pd.concat([good, bad], ignore_index=True))
    assert st.state == "error" and "lonely" in st.message and "m1.bin" in st.message
    assert not svc.series.exists("m1")


def test_nonexistent_spring_time_aborts():
    svc, st = ingest(make_frame([("2026-03-29 02:30:00", "a", 1.0), ("2026-03-29 02:30:01", "a", 2.0)]))
    assert st.state == "error" and "cannot convert" in st.message


def test_missing_columns_and_no_numeric_channel():
    svc, st = ingest(pd.DataFrame({"timestamp": [], "channel": []}))
    assert st.state == "error" and "lacks columns" in st.message
    frame = make_frame([("2026-05-12 10:00:00", "s", "a")])
    frame["value"] = frame["value"].astype(object)
    svc, st = ingest(frame)
    assert st.state == "error" and "no numeric" in st.message


def test_small_slices(regular_frame):
    svc, st = ingest(regular_frame, slice_rows=100)
    assert st.state == "done"
    assert len(svc.series.read_channel("m1", "a")[0]) == 600


def test_series_errors(regular_frame):
    svc = make_services(regular_frame)
    with pytest.raises(uc.NotReady):
        series(svc, ["a"])
    run_ingest(svc, "m1")
    with pytest.raises(uc.BadRequest):
        series(svc, ["nope"])
    with pytest.raises(uc.NotFound):
        uc.get_series(svc, "zzz", [("a", None)])


def test_series_window(regular_frame):
    svc, _ = ingest(regular_frame)
    s = series(svc, ["a"], j_start=10, j_end=12)["series"][0]
    assert s["j_start"] == 10 and s["values"] == [10.0, 11.0, 12.0]


def test_single_missing_sample_makes_a_hole_in_grid(regular_frame):
    f = regular_frame[~((regular_frame.channel == "a") & (regular_frame.value == 100))].reset_index(drop=True)
    svc, _ = ingest(f)
    v = series(svc, ["a"])["series"][0]["values"]
    assert v[100] is None and v[99] == 99.0 and v[101] == 101.0
