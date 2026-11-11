"""``machyon ui``: Machyon as a local web app, served from this computer to this computer's browser.

Imported only by the ``ui`` command, so every other command starts exactly as fast as before.

    machyon ui                       # http://127.0.0.1:8765, runs saved in the workspace chosen in the app
                                     # (~/machyon-downloads until one is chosen)
    machyon ui --output-dir D:/data  # this launch only: the chosen workspace is left as it is
"""
from .server import serve

__all__ = ["serve"]
