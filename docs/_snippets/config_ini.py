from pathlib import Path

import machyon
from machyon import DownloadConfig

Path("machyon.ini").write_text(
    "[SETTINGS]\n"
    "max_concurrency = 64\n"
    "max_retries = 8\n"
    "timeout = 20\n",
    encoding="utf-8",
)
cfg = DownloadConfig.from_ini("machyon.ini")   # the CLI takes the same file: --config machyon.ini
print(cfg.max_concurrency, cfg.max_retries, cfg.timeout)
report = machyon.download("urls.csv", "images/", config=cfg)
