"""Machyon - download any number of images, fast, and account for every one.

    import machyon
    report = machyon.download("urls.csv", "images/")
    print(report)

Docs: https://divyanshusingh96.github.io/machyon/
"""
from __future__ import annotations

import importlib.metadata

try:
    __version__ = importlib.metadata.version("machyon")
except importlib.metadata.PackageNotFoundError:  # running from a source checkout
    __version__ = "1.0.0.dev0"

from .compat import ImageDownloader, download_images
from .config import Config, DownloadConfig
from .downloader import Downloader, adownload, download
from .errors import FatalDiskError, MachyonError
from .limiter import AdaptiveLimiter
from .metrics import Report
from .naming import ImageRenamer, NameTemplate, generate_hashed_name
from .progress import ProgressSnapshot
from .sources import read_rows
from .utils import run, setup_logging

__all__ = [
    "download", "adownload", "Downloader", "DownloadConfig", "Report", "ProgressSnapshot",
    "NameTemplate", "read_rows", "run", "setup_logging",
    "MachyonError", "FatalDiskError", "AdaptiveLimiter",
    # class-based API
    "ImageDownloader", "ImageRenamer", "generate_hashed_name", "download_images", "Config",
    "__version__",
]
