"""Runs Machyon's public API with defaults on the harness rows."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import machyon  # noqa: E402

rows_path, save_dir, work = sys.argv[1:4]
rows = json.load(open(rows_path))
t0 = time.time()
r = machyon.download([(u, n) for _, u, n in rows], save_dir, progress=False,
                     profile_path=os.path.join(work, "profiles.json"))
print("RESULT " + json.dumps({"elapsed": round(time.time() - t0, 2), "report": str(r),
                              "hedged": r.hedged, "retries": r.retries, "final_conc": r.final_concurrency}))
