import machyon

# Split one big list across machines or sessions: rows [start, end) only.
# Every slice writes into the same folder and the same failure ledger, safely.
first = machyon.download("urls.csv", "images/", start=0, end=300, names="{hash:.2}/{hash}{ext}",
                         metrics_dir="runs/")
second = machyon.download("urls.csv", "images/", start=300, names="{hash:.2}/{hash}{ext}",
                          metrics_dir="runs/")
print(first)
print(second)
