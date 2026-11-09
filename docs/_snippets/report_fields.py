import json

import machyon

report = machyon.download("urls.csv", "images/")
print(f"{report.downloaded} new, {report.skipped} already there, {report.failed} failed")
print(f"{report.rate:.0f} img/s, {report.mb_per_s:.1f} MB/s, p95 latency {report.latency_p95:.2f} s")
print(f"concurrency settled at {report.final_concurrency}, peak {report.peak_concurrency}")
print(json.dumps(report.to_dict(), default=str)[:80], "...")
