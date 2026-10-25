"""Release checks used by the pipeline (RI-43).

    python scripts/release_check.py --ref refs/tags/v1.2.3     # tag format vMAJOR.MINOR.PATCH, newer than every other tag
    python scripts/release_check.py --wheel dist/viewer-*.whl   # the wheel contains the built frontend and the `viewer` command

A ref that is not a tag passes (only tags are released).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def parse(tag: str) -> tuple[int, int, int]:
    m = TAG.match(tag)
    if not m:
        raise ValueError(f"tag {tag!r} is not vMAJOR.MINOR.PATCH (for example v1.4.0)")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def check_tag(tag: str, others: list[str]) -> None:
    """Raises ValueError unless `tag` has the right format and is newer than every other well-formed tag."""
    version = parse(tag)
    newest = max((parse(t) for t in others if t != tag and TAG.match(t)), default=None)
    if newest is not None and version <= newest:
        raise ValueError(f"tag {tag} is not newer than v{'.'.join(map(str, newest))}")


def check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "viewer/static/index.html" not in names:
            raise ValueError(f"{path.name}: viewer/static/index.html missing (run scripts/build_frontend.py before building)")
        if not any(n.endswith("entry_points.txt") and b"viewer.cli:main" in z.read(n) for n in names):
            raise ValueError(f"{path.name}: the `viewer` console script is missing")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", help="git ref being built, e.g. refs/tags/v1.2.3")
    ap.add_argument("--wheel", nargs="+", type=Path)
    args = ap.parse_args(argv)
    try:
        if args.ref and args.ref.startswith("refs/tags/"):
            tags = subprocess.run(["git", "tag", "--list"], capture_output=True, text=True, check=True).stdout.split()
            check_tag(args.ref.removeprefix("refs/tags/"), tags)
            print(f"{args.ref}: ok")
        for w in args.wheel or []:
            check_wheel(w)
            print(f"{w.name}: ok")
    except ValueError as e:
        print("release check failed:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
