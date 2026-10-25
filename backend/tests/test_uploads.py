import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from viewer.api.app import create_app
from viewer.config import Settings
from viewer.mock.generate import SAMPLES, write_mock_bin

P = "/api/v1"
CHUNK = 20  # bytes: tiny blocks so that a ~100 byte fake .bin spans several of them
MB = 1024 * 1024


@pytest.fixture
def env(tmp_path):
    src = tmp_path / "src"
    write_mock_bin(src / "tiny.bin", SAMPLES["mock_tiny.bin"])
    settings = Settings(source_dir=src, storage_dir=tmp_path / "store", upload_chunk_mb=CHUNK / MB, max_upload_mb=1)
    with TestClient(create_app(settings)) as c:
        yield c, src, tmp_path


def mock_bytes(seed=11, **extra):
    return json.dumps({"format": "mock-bin-v1", "profile": "tiny", "seed": seed, "name": f"up{seed}", **extra}).encode()


def chunks(data: bytes):
    return [data[i : i + CHUNK] for i in range(0, len(data), CHUNK)]


def start(c, data, name="up.bin", fp="fp1"):
    r = c.post(f"{P}/uploads", json={"filename": name, "size": len(data), "fingerprint": fp})
    assert r.status_code == 200, r.text
    return r.json()


def put(c, uid, i, part, **headers):
    return c.put(f"{P}/uploads/{uid}/chunks/{i}", content=part, headers=headers)


def wait_done(c, mid, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        st = c.get(f"{P}/files/{mid}/ingest").json()
        if st["state"] in ("done", "error"):
            return st
        time.sleep(0.2)
    raise AssertionError("timeout")


def test_upload_convert_and_use(env):
    c, *_ = env
    data = mock_bytes()
    u = start(c, data, "My run.bin")
    assert u["chunk_size"] == CHUNK and u["total_chunks"] == -(-len(data) // CHUNK) > 2 and u["received"] == []
    for i, part in enumerate(chunks(data)):
        r = put(c, u["upload_id"], i, part, **{"X-Chunk-SHA256": hashlib.sha256(part).hexdigest()})
        assert r.status_code == 200 and r.json()["received"] == i + 1
    done = c.post(f"{P}/uploads/{u['upload_id']}/complete").json()
    assert done["id"] == hashlib.sha256(data).hexdigest()[:32]        # the content hash is the measurement id (RI-05)
    assert done == {"id": done["id"], "name": "My run.bin", "size": len(data), "existing": False}
    listed = {f["id"]: f for f in c.get(f"{P}/files").json()}
    assert listed[done["id"]]["name"] == "My run.bin"
    assert c.post(f"{P}/files/{done['id']}/ingest").json()["state"] in ("queued", "running")
    assert wait_done(c, done["id"])["state"] == "done"
    ch = c.get(f"{P}/files/{done['id']}/channels").json()
    assert ch["complete"] and any(x["name"] == "f10_000" for x in ch["channels"])
    r = c.post(f"{P}/files/{done['id']}/series", json={"channels": [{"name": "f10_000"}]})
    assert r.status_code == 200
    # the upload state is gone once complete
    assert c.get(f"{P}/uploads/{u['upload_id']}").status_code == 404


def test_resume_sends_only_the_missing_blocks(env):
    c, *_ = env
    data = mock_bytes(12)
    parts = chunks(data)
    u = start(c, data, fp="same")
    put(c, u["upload_id"], 0, parts[0])
    put(c, u["upload_id"], 2, parts[2])
    again = start(c, data, fp="same")                                   # the browser comes back after an interruption
    assert again["upload_id"] == u["upload_id"] and again["received"] == [0, 2]
    for i in range(len(parts)):
        if i not in again["received"]:
            assert put(c, u["upload_id"], i, parts[i]).status_code == 200
    assert c.post(f"{P}/uploads/{u['upload_id']}/complete").status_code == 200


def test_resume_survives_a_backend_restart(env):
    c, src, tmp = env
    data = mock_bytes(13)
    parts = chunks(data)
    u = start(c, data)
    put(c, u["upload_id"], 0, parts[0])
    settings = Settings(source_dir=src, storage_dir=tmp / "store", upload_chunk_mb=CHUNK / MB, max_upload_mb=1)
    with TestClient(create_app(settings)) as c2:                      # fresh process, same storage
        assert c2.get(f"{P}/uploads/{u['upload_id']}").json()["received"] == [0]


def test_chunk_is_idempotent_and_validated(env):
    c, *_ = env
    data = mock_bytes(14)
    parts = chunks(data)
    u = start(c, data)
    uid = u["upload_id"]
    assert put(c, uid, 0, parts[0]).status_code == 200 and put(c, uid, 0, parts[0]).status_code == 200
    assert c.get(f"{P}/uploads/{uid}").json()["received"] == [0]
    bad = put(c, uid, 1, parts[1][:-3])
    assert bad.status_code == 422 and "bytes" in bad.json()["detail"]
    corrupt = put(c, uid, 1, parts[1], **{"X-Chunk-SHA256": "0" * 64})
    assert corrupt.status_code == 422 and "corrupted" in corrupt.json()["detail"]
    assert put(c, uid, 99, parts[0]).status_code == 422
    assert put(c, "nope", 0, parts[0]).status_code == 404
    assert c.get(f"{P}/uploads/{uid}").json()["received"] == [0]       # nothing wrong was kept


def test_complete_with_missing_blocks_lists_them(env):
    c, *_ = env
    data = mock_bytes(15)
    parts = chunks(data)
    u = start(c, data)
    put(c, u["upload_id"], 0, parts[0])
    r = c.post(f"{P}/uploads/{u['upload_id']}/complete")
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["missing"] == list(range(1, len(parts))) and "missing" in d["message"]


def test_size_limit_and_empty_files(env):
    c, *_ = env
    r = c.post(f"{P}/uploads", json={"filename": "big.bin", "size": 2 * MB, "fingerprint": "x"})
    assert r.status_code == 413 and "largest" in r.json()["detail"] and "1 MB" in r.json()["detail"]
    assert c.post(f"{P}/uploads", json={"filename": "e.bin", "size": 0, "fingerprint": "x"}).status_code == 422


def test_cancel_discards_the_partial_upload(env):
    c, *_ = env
    data = mock_bytes(16)
    u = start(c, data)
    put(c, u["upload_id"], 0, chunks(data)[0])
    assert c.delete(f"{P}/uploads/{u['upload_id']}").status_code == 204
    assert c.get(f"{P}/uploads/{u['upload_id']}").status_code == 404
    assert start(c, data)["received"] == []


def test_identical_content_is_deduplicated(env):
    c, src, _ = env
    # same bytes as a file already in the source folder
    same = (src / "tiny.bin").read_bytes()
    u = start(c, same, "copy.bin")
    for i, part in enumerate(chunks(same)):
        put(c, u["upload_id"], i, part)
    done = c.post(f"{P}/uploads/{u['upload_id']}/complete").json()
    assert done["existing"] is True and done["name"] == "tiny.bin"
    assert len(c.get(f"{P}/files").json()) == 1
    # and uploading the same new file twice stores it once
    data = mock_bytes(17)
    ids = []
    for fp in ("a", "b"):
        u = start(c, data, "x.bin", fp)
        for i, part in enumerate(chunks(data)):
            put(c, u["upload_id"], i, part)
        ids.append(c.post(f"{P}/uploads/{u['upload_id']}/complete").json())
    assert ids[0]["id"] == ids[1]["id"] and [i["existing"] for i in ids] == [False, True]
    assert len(c.get(f"{P}/files").json()) == 2


def test_unrecognised_upload_fails_in_conversion_with_a_clear_message(env):
    c, *_ = env
    data = b"\x00\x01\x02 this is not a measurement file at all, just some bytes"
    u = start(c, data, "garbage.bin")
    for i, part in enumerate(chunks(data)):
        put(c, u["upload_id"], i, part)
    mid = c.post(f"{P}/uploads/{u['upload_id']}/complete").json()["id"]
    c.post(f"{P}/files/{mid}/ingest")
    st = wait_done(c, mid)
    assert st["state"] == "error" and "garbage.bin" in st["message"] and "not a recognised" in st["message"]


def test_file_names_are_sanitised(env):
    c, *_ = env
    data = mock_bytes(18)
    u = start(c, data, "../../etc/evil.bin")
    assert u["filename"] == "evil.bin"
    u2 = start(c, data, "C:\\Users\\me\\run 3.bin", "other")
    assert u2["filename"] == "run 3.bin"
