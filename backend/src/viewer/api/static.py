"""Serves the built frontend from the package (RI-41): hashed assets with long cache lifetime, everything else falls back to index.html."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
IMMUTABLE = "public, max-age=31536000, immutable"   # Vite names files in assets/ by content hash


def mount_static(app: FastAPI, directory: Path | None = None) -> bool:
    root = Path(directory or STATIC_DIR)
    index = root / "index.html"
    if not index.is_file():
        return False   # development: Vite serves the frontend

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or path == "api":
            raise HTTPException(404, "Not found")
        target = (root / path).resolve()
        if path and target.is_file() and root.resolve() in target.parents:
            return FileResponse(target, headers={"Cache-Control": IMMUTABLE} if path.startswith("assets/") else {"Cache-Control": "no-cache"})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})   # client-side routes and the entry page

    return True
