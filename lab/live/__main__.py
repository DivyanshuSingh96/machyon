"""python -m lab.live: start the Live Lab on http://127.0.0.1:8770/ and open it in your browser."""
from __future__ import annotations

import argparse
import secrets
import threading
import webbrowser

from aiohttp import web

from . import make_app


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m lab.live", description=__doc__)
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--token", help=argparse.SUPPRESS)  # tests only: a fixed session token
    a = ap.parse_args(argv)
    token = a.token or secrets.token_urlsafe(24)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Machyon Live Lab: {url}  (local only; press Ctrl+C to stop)", flush=True)
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    web.run_app(make_app(a.port, token), host="127.0.0.1", port=a.port, print=None)


if __name__ == "__main__":
    main()
