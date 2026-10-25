"""Start backend (uvicorn --reload, :8000) and frontend (vite, :5173) for development.

    python scripts/dev.py

Mock measurements are generated on first run if the source folder has no .bin files.
Settings come from VIEWER_* environment variables (defaults: data/source, data/storage).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
WIN = os.name == "nt"


def kill_tree(p: subprocess.Popen) -> None:
    if p.poll() is not None:
        return
    if WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    else:
        p.send_signal(signal.SIGTERM)


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "backend" / "src"))
    from viewer.config import Settings

    source = Settings.from_env().source_dir
    if not list(Path(source).glob("*.bin")):
        print(f"No measurements in {source}: generating mock files")
        subprocess.run([sys.executable, "-m", "viewer.mock.generate"], check=True, env={**os.environ, "PYTHONPATH": str(ROOT / "backend" / "src")})
    if not (FRONTEND / "node_modules").is_dir():
        print("Installing frontend dependencies")
        subprocess.run("npm install", cwd=FRONTEND, shell=True, check=True)

    backend = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "viewer.api.app:create_app", "--factory", "--reload",
         "--reload-dir", str(ROOT / "backend" / "src"), "--port", "8000"],
    )
    frontend = subprocess.Popen("npm run dev", cwd=FRONTEND, shell=True)
    print("\nBackend  http://127.0.0.1:8000/docs\nFrontend http://127.0.0.1:5173   (Ctrl+C to stop)\n")
    try:
        while backend.poll() is None and frontend.poll() is None:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        kill_tree(frontend)
        kill_tree(backend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
