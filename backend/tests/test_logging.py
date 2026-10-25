import json
import logging

import pytest
from fastapi.testclient import TestClient

from conftest import make_services
from viewer import __version__
from viewer.api.app import create_app
from viewer.config import Settings
from viewer.logging_config import JsonFormatter, TextFormatter, bind, configure_logging, request_id_var
from viewer.mock.generate import SAMPLES, write_mock_bin
from viewer.service.ingest import run_ingest

P = "/api/v1"


@pytest.fixture(autouse=True)
def restore_logging():
    yield
    configure_logging("INFO", "text")


def records(caplog, logger):
    return [r for r in caplog.records if r.name == logger]


def test_every_request_gets_an_id_returned_and_logged(caplog):
    caplog.set_level(logging.INFO, logger="viewer")
    c = TestClient(create_app(services=make_services()))
    r = c.get(f"{P}/version", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    rec = [x for x in records(caplog, "viewer.access") if x.path == f"{P}/version"][-1]
    assert rec.request_id == "abc-123" and rec.method == "GET" and rec.status == 200 and rec.ms >= 0 and rec.bytes > 0
    assert rec.route == f"{P}/version"
    generated = c.get(f"{P}/version").headers["x-request-id"]
    assert len(generated) == 16 and generated != "abc-123"


def test_hostile_request_ids_are_cleaned():
    c = TestClient(create_app(services=make_services()))
    r = c.get(f"{P}/version", headers={"X-Request-ID": "a b\tc;rm -rf/" + "x" * 200})
    assert r.headers["x-request-id"].isalnum() or set(r.headers["x-request-id"]) <= set("-_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert len(r.headers["x-request-id"]) <= 64


def test_user_comes_from_the_forwarded_identity_and_the_token_is_never_logged(caplog):
    caplog.set_level(logging.DEBUG, logger="viewer")
    c = TestClient(create_app(services=make_services()))
    r = c.get(f"{P}/me", headers={"X-Forwarded-Email": "ada@example.com", "X-Forwarded-Access-Token": "secret-token-123"})
    assert r.json() == {"user": "ada@example.com", "adapter": "local", "version": __version__}
    access = [x for x in records(caplog, "viewer.access") if x.path == f"{P}/me"][-1]
    assert access.user == "ada@example.com"
    assert "secret-token-123" not in caplog.text and "secret-token-123" not in JsonFormatter().format(access)
    assert c.get(f"{P}/me").json()["user"] is None


def test_version_endpoint():
    assert TestClient(create_app(services=make_services())).get(f"{P}/version").json() == {"version": __version__}


def test_json_format_has_the_fixed_fields_and_the_extras():
    rec = logging.LogRecord("viewer.x", logging.WARNING, __file__, 1, "something %s", ("happened",), None)
    rec.request_id, rec.user = "rid1", "u@x"
    rec.file, rec.cause = "run.bin", "boom"
    doc = json.loads(JsonFormatter().format(rec))
    assert doc["level"] == "WARNING" and doc["msg"] == "something happened" and doc["request_id"] == "rid1" and doc["user"] == "u@x"
    assert doc["file"] == "run.bin" and doc["cause"] == "boom" and doc["ts"].endswith("Z")
    text = TextFormatter().format(rec)
    assert "[rid1]" in text and "file=run.bin" in text and "cause=boom" in text


def test_configure_logging_is_idempotent_and_switches_format():
    configure_logging("DEBUG", "json")
    configure_logging("INFO", "text")
    lg = logging.getLogger("viewer")
    mine = [h for h in lg.handlers if getattr(h, "_viewer", False)]
    assert len(mine) == 1 and isinstance(mine[0].formatter, TextFormatter) and lg.level == logging.INFO


def test_failed_conversion_is_logged_with_file_channel_and_cause(caplog, regular_frame):
    import pandas as pd

    caplog.set_level(logging.INFO, logger="viewer")
    bind("req-77", "ada@example.com")
    bad = pd.DataFrame({"timestamp": pd.to_datetime(["2026-10-25 02:30:00"]).astype("datetime64[us]"), "channel": ["lonely"], "value": [1.0]})
    svc = make_services(bad)
    run_ingest(svc, "m1")
    rec = [r for r in records(caplog, "viewer.ingest") if r.levelno == logging.ERROR][-1]
    assert rec.request_id == "req-77" and rec.user == "ada@example.com"
    assert rec.file == "m1.bin" and rec.channel == "lonely" and rec.block and "lonely" in rec.cause and rec.measurement == "m1"
    request_id_var.set("-")


def test_successful_conversion_is_logged_with_counts(caplog, regular_frame):
    caplog.set_level(logging.INFO, logger="viewer")
    run_ingest(make_services(regular_frame), "m1")
    rec = [r for r in records(caplog, "viewer.ingest") if r.getMessage() == "ingestion done"][-1]
    assert rec.channels == 2 and rec.measurement == "m1" and rec.seconds >= 0


def test_spawned_conversion_keeps_the_request_id(tmp_path, capfd):
    """The child process logs with the id of the request that started it."""
    import time

    write_mock_bin(tmp_path / "src" / "tiny.bin", SAMPLES["mock_tiny.bin"])
    settings = Settings(source_dir=tmp_path / "src", storage_dir=tmp_path / "store", log_format="json")
    with TestClient(create_app(settings)) as c:
        mid = c.get(f"{P}/files").json()[0]["id"]
        c.post(f"{P}/files/{mid}/ingest", headers={"X-Request-ID": "trace-me-9"})
        for _ in range(300):
            if c.get(f"{P}/files/{mid}/ingest").json()["state"] == "done":
                break
            time.sleep(0.1)
    err = capfd.readouterr().err
    done = [json.loads(line) for line in err.splitlines() if line.startswith("{") and '"ingestion done"' in line]
    assert done and done[0]["request_id"] == "trace-me-9" and done[0]["measurement"] == mid


def test_upload_and_export_are_logged(caplog, regular_frame):
    from test_operator_service import two

    caplog.set_level(logging.INFO, logger="viewer")
    c = TestClient(create_app(services=two(regular_frame)))
    c.post(f"{P}/export", json={"format": "csv", "series": [{"label": "a", "file": "m1", "channel": "a", "mode": "step"}]})
    rec = [r for r in records(caplog, "viewer.api") if r.getMessage() == "export"][-1]
    assert rec.format == "csv" and rec.series == 1 and rec.rows == 600
