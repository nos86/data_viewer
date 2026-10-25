"""Write the OpenAPI schema (the backend/frontend contract, RI-37) to the frontend tree."""
import json
import sys
import tempfile
from pathlib import Path

from viewer.api.app import create_app
from viewer.config import Settings

out = Path(sys.argv[1] if len(sys.argv) > 1 else "frontend/src/api/openapi.json")
with tempfile.TemporaryDirectory() as tmp:
    app = create_app(Settings(source_dir=Path(tmp) / "s", storage_dir=Path(tmp) / "d"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
print("wrote", out)
