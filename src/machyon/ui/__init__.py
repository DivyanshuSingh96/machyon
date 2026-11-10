"""``machyon ui``: Machyon as a local web app, served from this computer to this computer's browser.

Imported only by the ``ui`` command, so every other command starts exactly as fast as before.

    machyon ui                       # http://127.0.0.1:8765, runs saved in ~/machyon-downloads
    machyon ui --output-dir D:/data  # keep the runs somewhere else
"""
from .server import serve

__all__ = ["serve"]
