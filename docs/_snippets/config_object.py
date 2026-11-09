import machyon
from machyon import DownloadConfig

polite = DownloadConfig(
    max_concurrency=16,                       # never more than 16 requests in flight
    max_retries=8,
    user_agent="my-dataset-bot/1.0 (+mailto:me@example.com)",
    headers={"Accept": "image/avif,image/webp,image/*"},
)
report = machyon.download("urls.csv", "images/", config=polite)

# DownloadConfig is a dataclass: derive variants without mutating the original.
strict = polite.replace(verify_existing=True, fsync=True)
print(report, strict.max_concurrency, strict.fsync)
