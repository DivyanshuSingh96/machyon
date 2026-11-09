import machyon

report = machyon.download("urls.csv", "images/")
print(report)                  # one-line summary
print(report.failure_reasons)  # e.g. {'http_404': 14, 'not_image': 6}
