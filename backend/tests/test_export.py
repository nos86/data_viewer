import io

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from conftest import make_services
from test_operator_service import two
from viewer.api.app import create_app
from viewer.core.models import MeasurementInfo, SourceData
from viewer.service import operators as svc_ops
from viewer.service.ingest import run_ingest

P = "/api/v1"


def client(svc):
    return TestClient(create_app(services=svc))


def post(c, series, fmt="csv", **kw):
    return c.post(f"{P}/export", json={"format": fmt, "series": series, **kw})


def sa(file, channel, label=None, mode="step"):
    return {"file": file, "channel": channel, "mode": mode, "label": label or f"{file}: {channel}"}


def read_csv(resp, **kw):
    return pd.read_csv(io.BytesIO(resp.content), **kw)


def test_long_format_on_the_channels_own_grid(regular_frame):
    c = client(two(regular_frame))
    r = post(c, [sa("m1", "a", "A: a"), sa("m1", "b", "A: b")], k_from=10, k_to=30)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and r.headers["content-type"].startswith("text/csv")
    df = read_csv(r)
    assert list(df.columns) == ["time_s", "channel", "value"]
    a = df[df.channel == "A: a"]
    b = df[df.channel == "A: b"]
    assert a.time_s.tolist() == [round(k / 10, 1) for k in range(10, 31)] and a.value.tolist() == list(map(float, range(10, 31)))   # 10 Hz grid
    assert b.time_s.tolist() == [1.0, 2.0, 3.0] and b.value.tolist() == [1.0, 2.0, 3.0]                                             # 1 Hz grid: no value is invented between its points


def test_holes_give_no_rows_and_nothing_is_filled(regular_frame):
    f = regular_frame[~((regular_frame.channel == "a") & (regular_frame.value.isin([100, 101])))].reset_index(drop=True)
    c = client(two(f))
    df = read_csv(post(c, [sa("m1", "a", "a")], k_from=98, k_to=104))
    assert df.time_s.tolist() == [9.8, 9.9, 10.2, 10.3, 10.4]          # 10.0 and 10.1 are holes: absent
    assert not df.value.isna().any()


def test_window_defaults_to_everything_and_may_be_empty(regular_frame):
    c = client(two(regular_frame))
    assert len(read_csv(post(c, [sa("m1", "a", "a")]))) == 600
    r = post(c, [sa("m1", "a", "a")], k_from=5000, k_to=6000)
    assert r.status_code == 200 and r.text.strip() == "time_s,channel,value"          # valid file, header only


def test_shifts_move_the_times_of_a_measurement(regular_frame):
    c = client(two(regular_frame))
    df = read_csv(post(c, [sa("m1", "b", "A"), sa("m2", "b", "B")], shifts={"m2": 25}, k_from=0, k_to=60))
    assert df[df.channel == "A"].time_s.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert df[df.channel == "B"].time_s.tolist() == [2.5, 3.5, 4.5, 5.5]               # own k·10 + 25 steps; never a rounded or merged time


def test_labels_with_commas_and_quotes_are_quoted(regular_frame):
    c = client(two(regular_frame))
    df = read_csv(post(c, [sa("m1", "a", 'ƒ "x, y"')], k_to=2))
    assert df.channel.unique().tolist() == ['ƒ "x, y"']


def test_spreadsheet_friendly_csv(regular_frame):
    svc = two(regular_frame)
    c = client(svc)
    r = post(c, [sa("m1", "a", "a")], k_from=1, k_to=3, csv={"decimal": ",", "delimiter": ";"})
    lines = r.content.decode().strip().splitlines()
    assert lines[0] == "time_s;channel;value" and lines[1] == "0,1;a;1"
    assert post(c, [sa("m1", "a", "a")], csv={"decimal": ",", "delimiter": ","}).status_code == 422


def test_parquet_has_the_same_rows(regular_frame):
    c = client(two(regular_frame))
    r = post(c, [sa("m1", "a", "a"), sa("m1", "b", "b")], "parquet", k_from=0, k_to=50)
    assert r.headers["content-type"] == "application/vnd.apache.parquet" and ".parquet" in r.headers["content-disposition"]
    t = pq.read_table(io.BytesIO(r.content))
    assert t.schema.names == ["time_s", "channel", "value"] and str(t.schema.field("time_s").type) == "double"
    df = t.to_pandas()
    assert len(df[df.channel == "a"]) == 51 and len(df[df.channel == "b"]) == 6
    csv = read_csv(post(c, [sa("m1", "a", "a"), sa("m1", "b", "b")], k_from=0, k_to=50))
    pd.testing.assert_frame_equal(df.reset_index(drop=True), csv, check_dtype=False)


def test_calculated_channels_by_handle_and_unknown_handle(regular_frame):
    svc = two(regular_frame)
    c = client(svc)
    r = svc_ops.apply(svc, "mul", [{"ch": {"file": "m1", "channel": "a", "mode": "step"}}, {"const": 2}], None, {})
    df = read_csv(post(c, [{"handle": r.handle, "label": "ƒ double"}], k_from=0, k_to=4))
    assert df.value.tolist() == [0.0, 2.0, 4.0, 6.0, 8.0] and df.channel.unique().tolist() == ["ƒ double"]
    u = post(c, [{"handle": "0" * 24, "label": "x"}])
    assert u.status_code == 404 and u.json()["detail"]["code"] == "unknown_handle"


def test_export_rejects_bad_requests(regular_frame):
    c = client(two(regular_frame))
    assert post(c, []).status_code == 422
    assert post(c, [{"label": "x"}]).status_code == 422                                  # neither file nor handle
    assert post(c, [sa("m1", "zzz")]).status_code == 422                                  # unknown channel
    assert post(c, [sa("nope", "a")]).status_code == 404                                  # unknown measurement
    assert client(make_services()).post(f"{P}/export", json={"format": "csv", "series": [sa("m1", "a")]}).status_code == 404


def test_filename_is_sanitised(regular_frame):
    c = client(two(regular_frame))
    r = post(c, [sa("m1", "a")], filename='../my "run" / 1')
    assert 'filename="' in r.headers["content-disposition"] and ".." not in r.headers["content-disposition"] and "/" not in r.headers["content-disposition"].split("filename=")[1]


def test_large_export_is_streamed_in_pieces(regular_frame, monkeypatch):
    from viewer.service import export as ex

    monkeypatch.setattr(ex, "PIECE", 100)
    c = client(two(regular_frame))
    r = post(c, [sa("m1", "a", "a")])
    assert len(read_csv(r)) == 600 and r.text.count("time_s") == 1             # one header, many pieces
