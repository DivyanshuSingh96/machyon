import machyon

report = machyon.download(
    "products.csv", "images/",
    url_col="image_link",                  # optional: guessed when omitted
    names="{label}/{index}_{hash}{ext}",   # a folder per label
)
print(report.downloaded, report.skipped, report.failed)
