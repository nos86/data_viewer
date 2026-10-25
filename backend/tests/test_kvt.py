"""Measurements from the metadata table and the KVT (local Delta folders): the same questions the Databricks adapters ask a warehouse."""
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from viewer.api.app import create_app
from viewer.config import Settings
from viewer.core.kvt import Filters, MetaRow, kvt_to_frame, parse_values, sql_get, sql_kvt, sql_list, where_clause
from viewer.mock.generator import generate_measurement
from viewer.mock.kvt import mock_uuid, write_measurement

P = "/api/v1"


def frame(seed=1, start="2026-03-02T08:30:00", **extra):
    f, _ = generate_measurement({"seed": seed, "start": start, "duration_s": 90, "channels": [
        {"name": "f10_000", "hz": 10, "missing": 0.01}, {"name": "f1_000", "hz": 1}, {"name": "ev_000", "kind": "event", "event_mean_s": 3}], **extra})
    return f


@pytest.fixture
def delta(tmp_path):
    out = tmp_path / "delta"
    write_measurement(out, mock_uuid("a"), frame(1, "2026-03-02T08:30:00"), "/data/run_a.bin", "VIN-1", "CFG-A")
    write_measurement(out, mock_uuid("b"), frame(2, "2026-05-12T10:00:00"), "/data/run_b.bin", "VIN-2", "CFG-B")
    write_measurement(out, mock_uuid("c"), frame(3, "2026-06-01T09:00:00"), "/data/run_c.bin", "VIN-1", "CFG-B")
    return out


def settings(tmp_path, delta, **kw):
    return Settings(source_dir=tmp_path / "src", storage_dir=tmp_path / "store", metadata_dir=delta / "metadata", kvt_dir=delta / "kvt", **kw)


def client(tmp_path, delta, **kw):
    return TestClient(create_app(settings(tmp_path, delta, **kw)))


def wait_done(c, mid, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"{P}/files/{mid}/ingest").json()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.2)
    raise AssertionError("timeout")


# ---- the shared definitions ---------------------------------------------------------------------------------


def test_filters_match_vin_configuration_and_start_inclusively():
    m = MetaRow("u", "/p", "VIN-1", "CFG-A", datetime(2026, 5, 1, 10), datetime(2026, 5, 1, 11))
    assert m.matches(None) and m.matches(Filters())
    assert m.matches(Filters(vin="vin-1")) and not m.matches(Filters(vin="VIN-2"))
    assert m.matches(Filters(configuration_id="cfg-a")) and not m.matches(Filters(configuration_id="CFG-B"))
    assert m.matches(Filters(start_from=datetime(2026, 5, 1, 10), start_to=datetime(2026, 5, 1, 10)))
    assert not m.matches(Filters(start_from=datetime(2026, 5, 1, 10, 0, 1)))
    assert not m.matches(Filters(start_to=datetime(2026, 4, 30)))
    assert not MetaRow("u", "", None, None, datetime(2026, 1, 1), datetime(2026, 1, 1)).matches(Filters(vin="x"))


def test_sql_is_built_from_the_same_filters():
    sql, params = sql_list("cat.sch.meta", Filters(vin="V", start_from=datetime(2026, 1, 1)))
    assert sql.startswith("SELECT uuid, path, vin, configurationId, startOfSampling, endOfSampling FROM cat.sch.meta WHERE")
    assert "lower(vin) = lower(?)" in sql and "startOfSampling >= ?" in sql and sql.endswith("ORDER BY startOfSampling DESC")
    assert params == ["V", datetime(2026, 1, 1)]
    assert sql_list("t", None) == ("SELECT uuid, path, vin, configurationId, startOfSampling, endOfSampling FROM t ORDER BY startOfSampling DESC", [])
    assert where_clause(Filters()) == ("", [])
    assert sql_get("t").endswith("WHERE uuid = ?")
    assert sql_kvt("t") == "SELECT key, value, timestamp FROM t WHERE uuid = ?"       # no ORDER BY: acquisition order is kept


def test_string_values_become_numbers_booleans_or_stay_text():
    ch = pd.Series(["n", "n", "b", "b", "t", "t"])
    v = pd.Series(["1.5", "2", "true", "False", "abc", "d"])
    out = parse_values(ch, v)
    assert out.tolist()[:4] == [1.5, 2.0, 1.0, 0.0] and out.tolist()[4:] == ["abc", "d"]
    assert parse_values(pd.Series(["a", "a"]), pd.Series(["1", "2"])).dtype == float


def test_kvt_arrow_to_extractor_frame():
    import pyarrow as pa

    t = pa.table({"key": ["x", "x"], "value": [1.0, 2.0], "timestamp": pa.array([np.datetime64("2026-01-01T00:00:00", "us")] * 2, pa.timestamp("us"))})
    df = kvt_to_frame(t)
    assert list(df.columns) == ["timestamp", "channel", "value"] and df["timestamp"].dtype == "datetime64[us]" and df["value"].dtype == float


# ---- local Delta tables ---------------------------------------------------------------------------------------


def test_metadata_table_is_the_catalogue_with_the_selector(tmp_path, delta):
    c = client(tmp_path, delta)
    files = c.get(f"{P}/files").json()
    assert [f["name"] for f in files] == ["run_c.bin", "run_b.bin", "run_a.bin"]                 # newest start first
    a = next(f for f in files if f["name"] == "run_a.bin")
    assert a["id"] == mock_uuid("a") and a["vin"] == "VIN-1" and a["configuration_id"] == "CFG-A" and a["start"].startswith("2026-03-02T08:30")
    assert {f["name"] for f in c.get(f"{P}/files", params={"vin": "vin-1"}).json()} == {"run_a.bin", "run_c.bin"}
    assert [f["name"] for f in c.get(f"{P}/files", params={"configuration_id": "CFG-B", "vin": "VIN-1"}).json()] == ["run_c.bin"]
    assert [f["name"] for f in c.get(f"{P}/files", params={"start_from": "2026-04-01T00:00:00", "start_to": "2026-05-31T00:00:00"}).json()] == ["run_b.bin"]
    assert c.get(f"{P}/files", params={"vin": "nobody"}).json() == []


def test_kvt_measurement_converts_and_serves_series(tmp_path, delta):
    c = client(tmp_path, delta)
    mid = mock_uuid("a")
    assert c.post(f"{P}/files/{mid}/ingest").json()["state"] in ("queued", "running")
    st = wait_done(c, mid)
    assert st["state"] == "done", st
    ch = {x["name"]: x for x in c.get(f"{P}/files/{mid}/channels").json()["channels"]}
    assert set(ch) == {"f10_000", "f1_000", "ev_000"} and ch["f10_000"]["n"] == 1 and ch["f1_000"]["n"] == 10 and ch["ev_000"]["event"]
    s = c.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "f10_000", "mode": "step"}]}).json()["series"][0]
    assert len(s["values"]) == st["duration_us"] // 100_000 + 1 and any(v is None for v in s["values"]) and any(v is not None for v in s["values"])


def test_t0_and_end_come_from_the_metadata_not_from_the_samples(tmp_path):
    out = tmp_path / "delta"
    f = frame(5, "2026-03-02T08:30:00")
    first, last = f["timestamp"].iloc[0], f["timestamp"].iloc[-1]
    write_measurement(out, mock_uuid("m"), f, "/m.bin", "V", "C", start=(first - pd.Timedelta(seconds=7)).to_pydatetime(), end=(last + pd.Timedelta(seconds=12)).to_pydatetime())
    c = client(tmp_path, out)
    st = (c.post(f"{P}/files/{mock_uuid('m')}/ingest"), wait_done(c, mock_uuid("m")))[1]
    expect_t0 = int(first.tz_localize("Europe/Rome").tz_convert("UTC").value // 1000) - 7_000_000
    assert st["state"] == "done" and st["t0_us"] == expect_t0
    assert st["duration_us"] == int((last - first).total_seconds() * 1e6) + 7_000_000 + 12_000_000          # RI-05: start .. end of sampling
    assert not st["warnings"]


def test_metadata_start_after_the_first_sample_is_ignored_with_a_warning(tmp_path):
    out = tmp_path / "delta"
    f = frame(6)
    write_measurement(out, mock_uuid("late"), f, "/late.bin", "V", "C", start=(f["timestamp"].iloc[0] + pd.Timedelta(seconds=30)).to_pydatetime())
    c = client(tmp_path, out)
    c.post(f"{P}/files/{mock_uuid('late')}/ingest")
    st = wait_done(c, mock_uuid("late"))
    assert st["state"] == "done" and any("startOfSampling" in w for w in st["warnings"])
    assert st["t0_us"] == int(f["timestamp"].iloc[0].tz_localize("Europe/Rome").tz_convert("UTC").value // 1000)


def test_fall_back_measurement_from_the_kvt(tmp_path):
    out = tmp_path / "delta"
    f, _ = generate_measurement({"seed": 3, "dst": True, "duration_s": 10800, "channels": [{"name": "a", "hz": 1}]})
    write_measurement(out, mock_uuid("dst"), f, "/dst.bin", "V", "C")
    c = client(tmp_path, out)
    c.post(f"{P}/files/{mock_uuid('dst')}/ingest")
    st = wait_done(c, mock_uuid("dst"))
    assert st["state"] == "done", st
    assert 10799_000_000 <= st["duration_us"] <= 10801_000_000                 # three real hours, although the wall clock repeats an hour


def test_string_valued_kvt_excludes_text_channels_and_reports_them(tmp_path):
    out = tmp_path / "delta"
    f = frame(7)
    f2 = f.copy()
    f2["value"] = f2["value"].astype(object)
    f2.loc[f2.channel == "f1_000", "value"] = "RUN"
    write_measurement(out, mock_uuid("s"), f2, "/s.bin", "V", "C", value_type="string")
    c = client(tmp_path, out)
    c.post(f"{P}/files/{mock_uuid('s')}/ingest")
    st = wait_done(c, mock_uuid("s"))
    assert st["state"] == "done" and any("f1_000" in w and "non-numeric" in w for w in st["warnings"])
    names = {x["name"] for x in c.get(f"{P}/files/{mock_uuid('s')}/channels").json()["channels"]}
    assert names == {"f10_000", "ev_000"}


def test_kvt_without_rows_is_a_clear_error(tmp_path):
    from viewer.mock.kvt import metadata_table
    from deltalake import write_deltalake

    out = tmp_path / "delta"
    f = frame(8)
    write_measurement(out, mock_uuid("x"), f, "/x.bin", "V", "C")
    write_deltalake(str(out / "metadata"), metadata_table([dict(uuid=mock_uuid("empty"), path="/empty.bin", vin="V", configurationId="C", start=datetime(2026, 1, 1), end=datetime(2026, 1, 2))]), mode="append")
    c = client(tmp_path, out)
    c.post(f"{P}/files/{mock_uuid('empty')}/ingest")
    st = wait_done(c, mock_uuid("empty"))
    assert st["state"] == "error" and "empty.bin" in st["message"] and "no rows" in st["message"]


def test_uploads_and_source_files_live_next_to_the_delta_measurements(tmp_path, delta):
    from viewer.mock.generate import SAMPLES, write_mock_bin

    write_mock_bin(tmp_path / "src" / "tiny.bin", SAMPLES["mock_tiny.bin"])
    c = client(tmp_path, delta)
    names = [f["name"] for f in c.get(f"{P}/files").json()]
    assert names[:3] == ["run_c.bin", "run_b.bin", "run_a.bin"] and "tiny.bin" in names
    assert c.get(f"{P}/files", params={"vin": "VIN-2"}).json()[0]["name"] == "run_b.bin"        # a filter hides files without metadata
    tiny = next(f for f in c.get(f"{P}/files").json() if f["name"] == "tiny.bin")
    c.post(f"{P}/files/{tiny['id']}/ingest")
    assert wait_done(c, tiny["id"])["state"] == "done"                                          # the binary path still works


def test_environment_variables_configure_the_tables(tmp_path, delta, monkeypatch):
    monkeypatch.setenv("VIEWER_METADATA_DIR", str(delta / "metadata"))
    monkeypatch.setenv("VIEWER_KVT_DIR", str(delta / "kvt"))
    s = Settings.from_env(source_dir=tmp_path / "src", storage_dir=tmp_path / "store")
    assert s.metadata_dir == delta / "metadata" and s.kvt_dir == delta / "kvt"
    assert Settings.from_env().metadata_dir is not None


def test_list_is_capped_most_recent_first(tmp_path, delta):
    c = client(tmp_path, delta)
    full = [f["id"] for f in c.get(f"{P}/files").json()]
    assert len(full) == 3 and full[0] == mock_uuid("c")                    # newest start first
    assert [f["id"] for f in c.get(f"{P}/files", params={"limit": 2}).json()] == full[:2]
    assert c.get(f"{P}/files", params={"limit": 0}).status_code == 422
