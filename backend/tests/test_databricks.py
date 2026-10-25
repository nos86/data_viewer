"""Databricks adapters against fakes: identity, permissions on the shared cache, filters reaching SQL, Volume persistence (RI-33…36, RI-40)."""
from datetime import datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fakes import FakeWarehouse, LocalFiles
from viewer.adapters.databricks import VolumeBlob, VolumeSeriesStore, build_databricks
from viewer.adapters.databricks.clients import ConnectorSqlClient, SdkFilesClient
from viewer.adapters.local import LocalSeriesStore
from viewer.api.app import create_app
from viewer.config import Settings
from viewer.core.models import Block
from viewer.mock.generator import generate_measurement
from viewer.mock.kvt import kvt_table, metadata_table, mock_uuid
from viewer.service.cache import SeriesCache
from viewer.service.context import Services
from viewer.service.ingest import run_ingest
from viewer.service.results import ResultStore

P = "/api/v1"
VOL = "/Volumes/c/s/v/viewer"
A, B = mock_uuid("a"), mock_uuid("b")
H_A = {"X-Forwarded-Access-Token": "tok-a", "X-Forwarded-Email": "a@example.com"}
H_B = {"X-Forwarded-Access-Token": "tok-b", "X-Forwarded-Email": "b@example.com"}


def frame(seed, start):
    f, _ = generate_measurement({"seed": seed, "start": start, "duration_s": 60, "channels": [{"name": "f10_000", "hz": 10}, {"name": "f1_000", "hz": 1}]})
    return f


@pytest.fixture
def env(tmp_path):
    import pyarrow as pa

    fa, fb = frame(1, "2026-03-02T08:30:00"), frame(2, "2026-05-12T10:00:00")

    def row(uid, f, vin, cfg):
        return dict(uuid=uid, path=f"/data/{vin}.bin", vin=vin, configurationId=cfg, start=f["timestamp"].iloc[0].to_pydatetime(), end=f["timestamp"].iloc[-1].to_pydatetime())

    meta = metadata_table([row(A, fa, "VIN-1", "CFG-A"), row(B, fb, "VIN-2", "CFG-B")])
    kvt = pa.concat_tables([kvt_table(A, fa, "double"), kvt_table(B, fb, "double")])
    wh = FakeWarehouse(meta, kvt, {"tok-a": {A}, "tok-b": {B}, "tok-all": "*"})
    files = LocalFiles(tmp_path / "vol")
    return wh, files


def make(tmp_path, env, **kw):
    wh, files = env
    settings = Settings(adapter="databricks", local_cache_dir=tmp_path / "cache", dbx_host="h", dbx_http_path="/sql", dbx_metadata_table="meta",
                        dbx_kvt_table="kvt", dbx_volume_path=VOL, source_dir=tmp_path / "src", storage_dir=tmp_path / "store", slice_rows=1000, **kw)
    svc = Services(settings, **build_databricks(settings, sql_factory=wh.client, files=files))
    svc.cache, svc.results = SeriesCache(64 * 2**20), ResultStore(64 * 2**20)
    svc.spawn_ingest = lambda mid, token=None: run_ingest(svc.for_user(token), mid)  # in-process, with the caller's token
    return TestClient(create_app(settings, services=svc)), svc


def ingest(c, mid, headers):
    assert c.post(f"{P}/files/{mid}/ingest", headers=headers).status_code in (200, 202)
    st = c.get(f"{P}/files/{mid}/ingest", headers=headers).json()
    assert st["state"] == "done", st


def test_no_token_is_unauthorised(tmp_path, env):
    c, _ = make(tmp_path, env)
    assert c.get(f"{P}/files").status_code == 401


def test_each_user_lists_only_what_unity_catalog_grants(tmp_path, env):
    c, _ = make(tmp_path, env)
    assert [f["id"] for f in c.get(f"{P}/files", headers=H_A).json()] == [A]
    assert [f["id"] for f in c.get(f"{P}/files", headers=H_B).json()] == [B]
    assert c.get(f"{P}/me", headers=H_A).json()["user"] == "a@example.com"


def test_cached_parquet_is_not_served_to_a_user_without_permission(tmp_path, env):
    c, _ = make(tmp_path, env, permission_ttl_s=0)
    ingest(c, A, H_A)
    body = {"channels": [{"name": "f10_000", "mode": "step"}]}
    assert c.post(f"{P}/files/{A}/series", json=body, headers=H_A).status_code == 200
    assert c.post(f"{P}/files/{A}/series", json=body, headers=H_B).status_code == 404
    assert c.get(f"{P}/files/{A}/channels", headers=H_B).status_code == 404


def test_permitted_user_reuses_the_shared_cache_without_reading_the_kvt_again(tmp_path, env):
    wh, _ = env
    c, _ = make(tmp_path, env)
    wh.grants["tok-a2"] = {A}
    ingest(c, A, H_A)
    n = wh.count("FROM kvt")
    h2 = {**H_A, "X-Forwarded-Access-Token": "tok-a2"}
    assert c.post(f"{P}/files/{A}/series", json={"channels": [{"name": "f1_000", "mode": "step"}]}, headers=h2).status_code == 200
    assert wh.count("FROM kvt") == n


def test_revoked_permission_takes_effect_after_the_ttl(tmp_path, env):
    wh, _ = env
    c, _ = make(tmp_path, env, permission_ttl_s=0)
    ingest(c, A, H_A)
    wh.grants["tok-a"] = set()
    assert c.get(f"{P}/files/{A}/channels", headers=H_A).status_code == 404


def test_uploaded_binaries_are_visible_to_every_user(tmp_path, env):
    c, svc = make(tmp_path, env)
    svc.blobs.write("uploads/abc.bin", b"x")
    svc.blobs.write("uploads/abc.json", b'{"name": "shared.bin"}')
    assert "abc" in [f["id"] for f in c.get(f"{P}/files", headers=H_A).json()]
    assert "abc" in [f["id"] for f in c.get(f"{P}/files", headers=H_B).json()]


def test_selector_filters_reach_the_sql_and_the_result(tmp_path, env):
    wh, _ = env
    c, _ = make(tmp_path, env)
    out = c.get(f"{P}/files", params={"vin": "vin-1", "configuration_id": "CFG-A", "start_from": "2026-01-01T00:00:00"}, headers={**H_A, "X-Forwarded-Access-Token": "tok-all"}).json()
    assert [f["id"] for f in out] == [A] and out[0]["vin"] == "VIN-1" and out[0]["configuration_id"] == "CFG-A"
    sql = [s for t, s, _ in wh.log if "lower(vin)" in s]
    assert sql and "startOfSampling >= ?" in sql[-1]


def test_missing_settings_are_reported_by_name(tmp_path):
    with pytest.raises(ValueError, match="VIEWER_DBX_HOST.*VIEWER_DBX_VOLUME_PATH"):
        build_databricks(Settings(adapter="databricks", local_cache_dir=tmp_path))


def test_series_persist_on_the_volume_and_restore_on_a_fresh_cache(tmp_path):
    files = LocalFiles(tmp_path / "vol")
    s1 = VolumeSeriesStore(files, VOL, LocalSeriesStore(tmp_path / "c1", slice_rows=100))
    w = s1.begin_write("m1")
    w.write_block(Block("a", np.array([30, 10, 20], np.int64), np.array([3.0, 1, 2])))
    w.commit()
    assert s1.ids() == ["m1"] and s1.size("m1") > 0
    s2 = VolumeSeriesStore(files, VOL, LocalSeriesStore(tmp_path / "c2", slice_rows=100))     # another app instance
    assert s2.exists("m1") and s2.channels("m1") == ["a"]
    s2.delete("m1")
    assert s1.ids() == [] and not s2.exists("m1")


def test_volume_blob_move_and_compose(tmp_path):
    b = VolumeBlob(LocalFiles(tmp_path / "vol"), VOL, tmp_path / "scratch")
    b.write("p/1", b"ab")
    b.write("p/2", b"cd")
    import hashlib

    assert b.compose("out", ["p/1", "p/2"]) == hashlib.sha256(b"abcd").hexdigest() and b.read("out") == b"abcd"
    b.move("out", "moved/out")
    assert not b.exists("out") and b.read("moved/out") == b"abcd" and b.size("moved/out") == 4
    with pytest.raises(KeyError):
        b.compose("x", ["p/1", "missing"])
    assert not list((tmp_path / "scratch").iterdir())


def test_connector_client_passes_token_and_params(monkeypatch):
    import sys
    import types

    import pyarrow as pa

    seen = {}

    class Cur:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params): seen["exec"] = (sql, params)
        def fetchall_arrow(self): return pa.table({"x": [1]})

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def cursor(self): return Cur()

    def connect(**kw):
        seen["connect"] = kw
        return Conn()

    mod = types.ModuleType("databricks"); sql = types.ModuleType("databricks.sql"); sql.connect = connect; mod.sql = sql
    monkeypatch.setitem(sys.modules, "databricks", mod); monkeypatch.setitem(sys.modules, "databricks.sql", sql)
    t = ConnectorSqlClient("https://h.cloud.databricks.com/", "/sql/1", "tok").query("SELECT ?", [1])
    assert t.num_rows == 1 and seen["connect"] == dict(server_hostname="h.cloud.databricks.com", http_path="/sql/1", access_token="tok") and seen["exec"] == ("SELECT ?", [1])


def test_sdk_files_client_maps_not_found_to_false(monkeypatch):
    import sys
    import types

    class NotFound(Exception):
        pass

    class Files:
        def get_metadata(self, p): raise NotFound()
        def delete(self, p): raise NotFound()
        def list_directory_contents(self, d): raise NotFound()

    class WS:
        def __init__(self, **kw): self.files = Files()

    sdk = types.ModuleType("databricks.sdk"); sdk.WorkspaceClient = WS
    monkeypatch.setitem(sys.modules, "databricks", types.ModuleType("databricks")); monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)
    c = SdkFilesClient("h", "t")
    assert c.exists("/Volumes/x") is False and c.list_files("/Volumes") == []
    c.delete("/Volumes/x")
