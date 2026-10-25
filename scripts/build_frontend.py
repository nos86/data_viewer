"""Builds the frontend and copies it into the Python package, so that a wheel contains the whole application (RI-41).

    python scripts/build_frontend.py [--skip-npm]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
TARGET = ROOT / "backend" / "src" / "viewer" / "static"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-npm", action="store_true", help="reuse frontend/dist")
    args = ap.parse_args()
    if not args.skip_npm:
        if not (FRONTEND / "node_modules").is_dir():
            subprocess.run("npm ci", cwd=FRONTEND, shell=True, check=True)
        subprocess.run("npm run build", cwd=FRONTEND, shell=True, check=True)
    dist = FRONTEND / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit(f"{dist}/index.html is missing: build the frontend first")
    shutil.rmtree(TARGET, ignore_errors=True)
    shutil.copytree(dist, TARGET)
    print(f"copied {dist} -> {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
