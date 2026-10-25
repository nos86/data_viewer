"""End-to-end through the HTTP API with the local adapters and a real separate ingestion process."""
import time

import pytest
from fastapi.testclient import TestClient

from viewer.api.app import create_app
from viewer.config import Settings
from viewer.mock.generate import SAMPLES, write_mock_bin

P = "/api/v1"


@pytest.fixture
def client(tmp_path):
    write_mock_bin(tmp_path / "src" / "tiny.bin", SAMPLES["mock_tiny.bin"])
    (tmp_path / "src" / "bad.bin").write_bytes(b"\x00not a measurement")
    settings = Settings(source_dir=tmp_path / "src", storage_dir=tmp_path / "store")
    with TestClient(create_app(settings)) as c:
        yield c


def wait_done(client, mid, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get(f"{P}/files/{mid}/ingest").json()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.2)
    raise AssertionError("ingestion timed out")


def file_id(client, name):
    return next(f["id"] for f in client.get(f"{P}/files").json() if f["name"] == name)


def test_full_flow(client):
    files = client.get(f"{P}/files").json()
    assert {f["name"] for f in files} == {"tiny.bin", "bad.bin"}
    assert all(f["state"] == "not_ingested" for f in files)
    mid = file_id(client, "tiny.bin")

    # not converted yet
    assert client.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "f10_000"}]}).status_code == 409
    prov = client.get(f"{P}/files/{mid}/channels").json()
    assert prov["complete"] is False and prov["channels"] == []

    assert client.post(f"{P}/files/{mid}/ingest").json()["state"] in ("queued", "running")
    st = wait_done(client, mid)
    assert st["state"] == "done", st
    assert st["duration_us"] > 100_000_000 and st["t0_us"] > 0
    assert any("state_000" in w for w in st["warnings"])
    assert client.post(f"{P}/files/{mid}/ingest").json()["state"] == "done"   # idempotent

    chans = client.get(f"{P}/files/{mid}/channels").json()
    assert chans["complete"]
    by = {c["name"]: c for c in chans["channels"]}
    assert by["f100_000"]["n"] == 1 and by["f100_000"]["native_hz"] == pytest.approx(100, rel=0.01)
    assert by["f3_000"]["n"] == 10 and by["f3_000"]["assigned_hz"] == 1.0
    assert by["ev_000"]["event"] is True and by["ev_000"]["suggested_mode"] == "step"
    assert "state_000" not in by and "flag_000" in by

    r = client.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "f10_000", "mode": "step"}, {"name": "f1_000"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["step_base_us"] == 100_000
    s10, s1 = body["series"]
    assert s10["channel"] == "f10_000" and s10["n"] == 1 and s10["j_start"] == 0
    assert len(s10["values"]) == st["duration_us"] // 100_000 + 1
    assert None in s10["values"] and any(v is not None for v in s10["values"])
    assert s1["n"] == 10 and len(s1["values"]) == st["duration_us"] // 1_000_000 + 1

    # default modes: linear for regular channels, step for event channels
    r = client.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "f1_000"}, {"name": "ev_000"}]})
    assert [x["mode"] for x in r.json()["series"]] == ["linear", "step"]
    # average is refused for event channels, allowed for regular ones
    r = client.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "ev_000", "mode": "average"}]})
    assert r.status_code == 422 and "event" in r.json()["detail"]
    assert by["ev_000"]["modes_allowed"] == ["step", "linear"] and "average" in by["f1_000"]["modes_allowed"]
    avg = client.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "f10_000", "mode": "average"}]})
    assert avg.status_code == 200 and avg.json()["series"][0]["mode"] == "average"

    # Arrow IPC carries the same values as JSON
    import io, json as _json
    import numpy as np
    import pyarrow as pa
    body = {"channels": [{"name": "f10_000", "mode": "linear"}, {"name": "f1_000", "mode": "average"}]}
    rj = client.post(f"{P}/files/{mid}/series", json=body).json()
    ra = client.post(f"{P}/files/{mid}/series", json=body, headers={"Accept": "application/vnd.apache.arrow.stream"})
    assert ra.headers["content-type"].startswith("application/vnd.apache.arrow.stream")
    meta = _json.loads(ra.headers["x-series-meta"])
    batches = list(pa.ipc.open_stream(io.BytesIO(ra.content)))
    assert [m["channel"] for m in meta["series"]] == ["f10_000", "f1_000"] and meta["step_base_us"] == 100_000
    for m, b, j in zip(meta["series"], batches, rj["series"]):
        got = b.column(0).to_pylist()
        assert len(got) == m["length"] == len(j["values"]) and m["j_start"] == j["j_start"] and m["n"] == j["n"]
        assert got == j["values"]

    r = client.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "f10_000"}], "j_start": 5, "j_end": 9})
    assert r.json()["series"][0]["j_start"] == 5 and len(r.json()["series"][0]["values"]) == 5


def test_errors(client):
    assert client.post(f"{P}/files/zzz/ingest").status_code == 404
    mid = file_id(client, "tiny.bin")
    assert client.post(f"{P}/files/{mid}/series", json={"channels": []}).status_code == 422
    assert client.post(f"{P}/files/{mid}/series", json={"channels": [{"name": "a", "mode": "bogus"}]}).status_code == 422


def test_unrecognised_file_reports_clear_error(client):
    mid = file_id(client, "bad.bin")
    client.post(f"{P}/files/{mid}/ingest")
    st = wait_done(client, mid)
    assert st["state"] == "error" and "bad.bin" in st["message"] and "not a recognised" in st["message"]


def test_openapi_schema_exposes_v1_paths(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert f"{P}/files" in paths and f"{P}/files/{{mid}}/series" in paths


def test_operators_api(client):
    import io
    import json as _json
    import numpy as np
    import pyarrow as pa

    cat = client.get(f"{P}/operators").json()
    by = {o["name"]: o for o in cat}
    assert {"add", "gt", "derivative", "moving_average", "magnitude", "select"} <= set(by)
    assert by["moving_average"]["params"][0]["name"] == "window_s" and by["gt"]["output"] == "discrete"

    mid = file_id(client, "tiny.bin")
    body = {"inputs": [{"ch": {"file": mid, "channel": "f10_000", "mode": "linear"}}, {"ch": {"file": mid, "channel": "f5_000", "mode": "linear"}}]}
    assert client.post(f"{P}/operators/sub", json=body).status_code == 409          # not converted yet
    client.post(f"{P}/files/{mid}/ingest")
    assert wait_done(client, mid)["state"] == "done"

    r = client.post(f"{P}/operators/sub", json=body)
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["operator"] == "sub" and first["n"] == 2 and first["series"] is None
    assert first["stats"]["count"] > 0 and len(first["sparkline"]) <= 200 and len(first["handle"]) == 24
    assert client.post(f"{P}/operators/sub", json=body).json()["handle"] == first["handle"]

    # chain through the handle; full result as JSON and as Arrow carry the same values
    chain = {"inputs": [{"handle": first["handle"]}], "params": {"window_s": 1.0}, "detail": "full"}
    rj = client.post(f"{P}/operators/moving_average", json=chain).json()
    assert rj["series"]["file"] == "~calc" and rj["series"]["n"] == 2 and len(rj["series"]["values"]) == rj["length"]
    ra = client.post(f"{P}/operators/moving_average", json=chain, headers={"Accept": "application/vnd.apache.arrow.stream"})
    meta = _json.loads(ra.headers["x-series-meta"])
    assert meta["result"]["handle"] == rj["handle"] and meta["series"][0]["length"] == rj["length"]
    got = list(pa.ipc.open_stream(io.BytesIO(ra.content)))[0].column(0).to_pylist()
    assert got == rj["series"]["values"]

    # errors are explicit
    u = client.post(f"{P}/operators/neg", json={"inputs": [{"handle": "0" * 24}]})
    assert u.status_code == 404 and u.json()["detail"]["code"] == "unknown_handle" and u.json()["detail"]["handle"] == "0" * 24
    assert client.post(f"{P}/operators/nope", json=body).status_code == 422
    assert client.post(f"{P}/operators/moving_average", json={"inputs": body["inputs"][:1], "params": {"window_s": -3}}).status_code == 422
    assert client.post(f"{P}/operators/add", json={"inputs": [{"const": 1}, {"const": 2}]}).status_code == 422
    assert client.post(f"{P}/operators/add", json={"inputs": [{"const": 1}]}).status_code == 422                    # arity
    assert client.post(f"{P}/operators/add", json={"inputs": [{"ch": {"file": mid, "channel": "zzz"}}, {"const": 1}]}).status_code == 422
    assert client.post(f"{P}/operators/add", json={"inputs": [{"ch": {"file": "nope", "channel": "a"}}, {"const": 1}]}).status_code == 404
