import json
from collections import Counter

import machyon

machyon.download("urls.csv", "images/")

# One JSON object per line: {"url", "names", "reason", "error"?}
with open("images/failed_downloads.jsonl", encoding="utf-8") as f:
    failures = [json.loads(line) for line in f]

print(Counter(entry["reason"] for entry in failures))
dead = [entry["url"] for entry in failures if entry["reason"] == "http_404"]
print(len(dead), "links are gone for good")
