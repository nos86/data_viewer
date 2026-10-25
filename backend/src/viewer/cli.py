"""Command line: `viewer serve | mock | version` (RI-41)."""
from __future__ import annotations

import argparse
import threading
import webbrowser

from viewer import __version__


def serve(host: str, port: int, open_browser: bool) -> None:
    import uvicorn

    from viewer.api.static import STATIC_DIR

    if not (STATIC_DIR / "index.html").is_file():
        print("note: this install has no built frontend (viewer/static); only the API is served. Build it with scripts/build_frontend.py")
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{shown}:{port}"
    print(f"viewer {__version__} at {url}   (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run("viewer.api.app:create_app", factory=True, host=host, port=port, log_config=None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="viewer", description="Measurement viewer. Settings come from VIEWER_* environment variables.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="start the web application")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-browser", action="store_true", help="do not open the browser")
    m = sub.add_parser("mock", help="write mock measurements (fake .bin files, or Delta tables with --kvt)")
    m.add_argument("--large", action="store_true", help="include the 50M-sample benchmark measurement")
    m.add_argument("--kvt", action="store_true", help="write metadata + KVT Delta tables (needs the 'delta' extra) instead of .bin files")
    m.add_argument("--out", default=None, help="output folder")
    sub.add_parser("version", help="print the version")
    args = ap.parse_args(argv)

    if args.cmd == "version":
        print(__version__)
    elif args.cmd == "serve":
        serve(args.host, args.port, not args.no_browser)
    elif args.cmd == "mock":
        extra = (["--out", args.out] if args.out else []) + (["--large"] if args.large else [])
        if args.kvt:
            from viewer.mock import kvt

            kvt.main(extra)
        else:
            from viewer.mock import generate

            generate.main(extra)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
