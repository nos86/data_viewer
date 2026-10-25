"""Static serving, command line and wheel contents (RI-41)."""
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from viewer import __version__
from viewer.api.static import mount_static
from viewer.cli import main

BACKEND = Path(__file__).resolve().parent.parent


@pytest.fixture
def site(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "a-1.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("x")
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping():
        return {"ok": True}

    assert mount_static(app, tmp_path)
    return TestClient(app)


def test_static_files_spa_fallback_and_api_precedence(site):
    assert site.get("/").text == "<html>app</html>"
    assert site.get("/some/client/route").text == "<html>app</html>"
    js = site.get("/assets/a-1.js")
    assert js.text == "console.log(1)" and "immutable" in js.headers["cache-control"]
    assert site.get("/").headers["cache-control"] == "no-cache"
    assert site.get("/api/v1/ping").json() == {"ok": True}
    assert site.get("/api/v1/unknown").status_code == 404          # the fallback never answers for the API
    assert site.get("/%2e%2e/secret.txt").text == "<html>app</html>"   # path traversal never leaves the folder


def test_no_static_folder_leaves_the_app_alone(tmp_path):
    assert not mount_static(FastAPI(), tmp_path / "missing")


def test_cli_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_serve_without_browser_answers_the_version(tmp_path):
    import httpx

    port = free_port()
    p = subprocess.Popen([sys.executable, "-m", "viewer.cli", "serve", "--no-browser", "--port", str(port)], cwd=tmp_path,
                         env={**__import__("os").environ, "VIEWER_SOURCE_DIR": str(tmp_path / "src"), "VIEWER_STORAGE_DIR": str(tmp_path / "st")},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/api/v1/version", timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            raise AssertionError("server did not start")
        assert r.json() == {"version": __version__}
    finally:
        p.terminate()
        p.wait(10)


def test_wheel_contains_the_frontend_and_the_command(tmp_path):
    pytest.importorskip("hatch_vcs")
    pytest.importorskip("build")
    if not (BACKEND / "src" / "viewer" / "static" / "index.html").is_file():
        pytest.skip("frontend not built: run scripts/build_frontend.py")
    r = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path)], cwd=BACKEND, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-2000:]
    wheel = next(tmp_path.glob("viewer-*.whl"))
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        assert "viewer/static/index.html" in names and any(n.startswith("viewer/static/assets/") for n in names)
        assert any(n.endswith("entry_points.txt") and b"viewer.cli:main" in z.read(n) for n in names)
