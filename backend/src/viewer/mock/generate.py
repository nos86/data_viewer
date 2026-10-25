"""Write sample fake .bin files into the configured source folder.

    python -m viewer.mock.generate [--out DIR] [--large]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from viewer.config import Settings

SAMPLES = {
    "mock_tiny.bin": {"profile": "tiny", "seed": 1, "name": "mock_tiny"},
    "mock_small.bin": {"profile": "small", "seed": 2, "name": "mock_small"},
    "mock_dst_2026-10-25.bin": {"profile": "tiny", "seed": 3, "dst": True, "name": "mock_dst"},
}
LARGE = {"mock_large_50M.bin": {"profile": "large", "seed": 4, "name": "mock_large"}}


def write_mock_bin(path: Path, params: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": "mock-bin-v1", **params}, indent=1), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=None, help="default: VIEWER_SOURCE_DIR or data/source")
    ap.add_argument("--large", action="store_true", help="also write the 50M-sample benchmark file")
    args = ap.parse_args(argv)
    out = args.out or Settings.from_env().source_dir
    for name, params in {**SAMPLES, **(LARGE if args.large else {})}.items():
        print("wrote", write_mock_bin(out / name, params))


if __name__ == "__main__":
    main()
