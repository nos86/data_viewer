"""Contract tests (RI-36): every adapter of each interface must pass the same suite."""
import json

import numpy as np
import pytest

from fakes import FakeWarehouse, LocalFiles
from viewer.adapters.databricks import BlobFileSource, BlobUploadCatalog, DbxCatalog, DbxSource, VolumeBlob, VolumeSeriesStore

from viewer.adapters.local import LocalBlob, LocalCatalog, LocalSeriesStore, LocalSource, _FileIndex
from viewer.adapters.memory import MemoryBlob, MemoryCatalog, MemorySeriesStore, MemorySource
from viewer.core.models import Block, IngestError, MeasurementInfo, SourceData
from viewer.mock.generate import SAMPLES, write_mock_bin
from viewer.mock.generator import generate_measurement

# ---- BlobStorage -------------------------------------------------------------------------------


@pytest.fixture(params=["memory", "local", "databricks"])
def blob(request, tmp_path):
    if request.param == "memory":
        return MemoryBlob()
    if request.param == "local":
        return LocalBlob(tmp_path / "blobs")
    return VolumeBlob(LocalFiles(tmp_path / "vol"), "/Volumes/c/s/v/viewer", tmp_path / "scratch")   # Volume adapter over a faked Files API


def test_blob_roundtrip_overwrite_delete(blob):
    assert not blob.exists("a/x.json")
    blob.write("a/x.json", b"1")
    blob.write("a/x.json", b"22")
    assert blob.exists("a/x.json") and blob.read("a/x.json") == b"22"
    blob.delete("a/x.json")
    assert not blob.exists("a/x.json")
    blob.delete("a/x.json")  # idempotent


def test_blob_missing_raises_keyerror(blob):
    with pytest.raises(KeyError):
        blob.read("nope")


def test_blob_list_by_prefix(blob):
    for k in ("a/1", "a/2", "b/1"):
        blob.write(k, b"")
    assert blob.list("a/") == ["a/1", "a/2"]
    assert blob.list() == ["a/1", "a/2", "b/1"]


# ---- SeriesStore -------------------------------------------------------------------------------


@pytest.fixture(params=["memory", "local", "databricks"])
def store(request, tmp_path):
    if request.param == "memory":
        return MemorySeriesStore()
    local = LocalSeriesStore(tmp_path / "pq", slice_rows=4)
    if request.param == "local":
        return local
    return VolumeSeriesStore(LocalFiles(tmp_path / "vol"), "/Volumes/c/s/v/viewer", LocalSeriesStore(tmp_path / "cache", slice_rows=4))


def filled(store, mid="m"):
    w = store.begin_write(mid)
    w.write_block(Block("b", np.array([30, 10, 20], np.int64), np.array([3.0, 1, 2])))
    w.write_block(Block("a", np.array([5, 1], np.int64), np.array([0.5, 0.1])))
    w.write_block(Block("b", np.array([40, 50, 60], np.int64), np.array([4.0, 5, 6])))
    w.commit()
    return mid


def test_series_sorted_by_t_per_channel(store):
    mid = filled(store)
    assert store.exists(mid) and store.channels(mid) == ["a", "b"]
    t, v = store.read_channel(mid, "b")
    assert t.tolist() == [10, 20, 30, 40, 50, 60] and v.tolist() == [1, 2, 3, 4, 5, 6]
    assert t.dtype == np.int64 and v.dtype == np.float64
    assert store.read_channel(mid, "a")[0].tolist() == [1, 5]


def test_series_interval_is_inclusive(store):
    mid = filled(store)
    t, v = store.read_channel(mid, "b", 20, 50)
    assert t.tolist() == [20, 30, 40, 50]
    assert store.read_channel(mid, "b", t_from_us=45)[0].tolist() == [50, 60]
    assert store.read_channel(mid, "b", t_to_us=15)[0].tolist() == [10]


def test_series_unknown_channel_or_measurement_is_empty(store):
    mid = filled(store)
    assert store.read_channel(mid, "zzz")[0].size == 0
    assert store.read_channel("other", "a")[0].size == 0
    assert not store.exists("other") and store.channels("other") == []


def test_series_not_visible_before_commit_and_abort(store):
    w = store.begin_write("x")
    w.write_block(Block("a", np.array([1], np.int64), np.array([1.0])))
    assert not store.exists("x")
    w.abort()
    assert not store.exists("x")


def test_series_delete_and_rewrite(store):
    mid = filled(store)
    store.delete(mid)
    assert not store.exists(mid)
    filled(store, mid)
    assert store.exists(mid)


# ---- MeasurementCatalog + MeasurementSource ----------------------------------------------------


@pytest.fixture(params=["memory", "local", "kvt-local", "databricks", "databricks-uploads"])
def measurement(request, tmp_path):
    """(catalog, source, id) with one measurement."""
    if request.param == "memory":
        frame, meta = generate_measurement({"profile": "tiny", "seed": 1, "duration_s": 20})
        info = MeasurementInfo("id1", "m.bin", "mem://m")
        return MemoryCatalog([info]), _mem_source("id1", frame, meta), "id1"
    if request.param in ("kvt-local", "databricks"):
        from viewer.mock.kvt import kvt_table, metadata_table, mock_uuid, write_measurement

        frame, _ = generate_measurement({"profile": "tiny", "seed": 1, "duration_s": 20})
        uid = mock_uuid("contract")
        if request.param == "kvt-local":
            from viewer.adapters.local.kvt import DeltaFolders, KvtCatalog, KvtSource

            write_measurement(tmp_path / "d", uid, frame, "/data/contract.bin", "VIN-1", "CFG-A")
            tables = DeltaFolders(tmp_path / "d" / "metadata", tmp_path / "d" / "kvt")
            cat = KvtCatalog(tables)
            return cat, KvtSource(tables, cat), uid
        row = dict(uuid=uid, path="/data/contract.bin", vin="VIN-1", configurationId="CFG-A", start=frame["timestamp"].iloc[0].to_pydatetime(), end=frame["timestamp"].iloc[-1].to_pydatetime())
        wh = FakeWarehouse(metadata_table([row]), kvt_table(uid, frame, "double"), {"tok": "*"})
        cat = DbxCatalog(wh.client("tok"), "meta")
        return cat, DbxSource(wh.client("tok"), cat, "kvt"), uid
    if request.param == "databricks-uploads":
        blobs = VolumeBlob(LocalFiles(tmp_path / "vol"), "/Volumes/c/s/v/viewer", tmp_path / "scratch")
        raw = json.dumps({"format": "mock-bin-v1", "profile": "tiny", "seed": 1, "duration_s": 20}).encode()
        blobs.write("uploads/abc123.bin", raw)
        blobs.write("uploads/abc123.json", b'{"name": "from-upload.bin"}')
        cat = BlobUploadCatalog(blobs)
        return cat, BlobFileSource(blobs, cat, tmp_path / "scratch2"), "abc123"
    write_mock_bin(tmp_path / "one.bin", {**SAMPLES["mock_tiny.bin"], "duration_s": 20})
    idx = _FileIndex(tmp_path)
    cat = LocalCatalog(idx)
    return cat, LocalSource(idx), cat.list()[0].id


def _mem_source(mid, frame, meta):
    s = MemorySource()
    s.add(mid, SourceData(frame, meta))
    return s


def test_catalog_lists_and_gets(measurement):
    cat, _, mid = measurement
    items = cat.list()
    assert [m.id for m in items] == [mid]
    got = cat.get(mid)
    assert got is not None and got.id == mid and got.name.endswith(".bin")
    assert cat.get("unknown") is None


def test_source_returns_extractor_contract(measurement):
    _, src, mid = measurement
    data = src.read(mid)
    assert list(data.frame.columns) == ["timestamp", "channel", "value"]
    assert data.frame["timestamp"].dtype.kind == "M"
    assert {"vin", "datetime", "signals"} <= set(data.meta)
    assert set(data.frame["channel"]) <= set(data.meta["signals"])


def test_source_unknown_id(measurement):
    _, src, _ = measurement
    with pytest.raises(KeyError):
        src.read("unknown")


def test_local_source_reports_unrecognised_file(tmp_path):
    (tmp_path / "bad.bin").write_bytes(b"\x00garbage")
    idx = _FileIndex(tmp_path)
    mid = LocalCatalog(idx).list()[0].id
    with pytest.raises(IngestError, match="bad.bin"):
        LocalSource(idx).read(mid)


# ---- step 6 additions: size / move / compose / ids ----------------------------------------------------------


def test_blob_size_and_move(blob):
    blob.write("a/x", b"12345")
    assert blob.size("a/x") == 5
    blob.move("a/x", "b/y")
    assert not blob.exists("a/x") and blob.read("b/y") == b"12345"
    blob.write("b/z", b"zz")
    blob.move("b/y", "b/z")                                  # replaces the destination
    assert blob.read("b/z") == b"12345"
    with pytest.raises(KeyError):
        blob.size("nope")
    with pytest.raises(KeyError):
        blob.move("nope", "other")


def test_blob_compose_concatenates_in_order_and_returns_the_sha256(blob):
    import hashlib

    for i, part in enumerate([b"abc", b"", b"defg", b"h"]):
        blob.write(f"p/{i}", part)
    digest = blob.compose("out/all.bin", [f"p/{i}" for i in range(4)])
    assert blob.read("out/all.bin") == b"abcdefgh"
    assert digest == hashlib.sha256(b"abcdefgh").hexdigest()
    with pytest.raises(KeyError):
        blob.compose("out/x.bin", ["p/0", "p/missing"])


def test_series_size_and_ids(store):
    assert store.size("m") == 0 and store.ids() == []
    mid = filled(store)
    assert store.size(mid) > 0 and store.ids() == [mid]
    store.delete(mid)
    assert store.size(mid) == 0 and store.ids() == []
