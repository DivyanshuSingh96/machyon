# needs: pip install "machyon[parquet]"
import machyon

# Parquet is read in batches of 65,536 rows, so the file never has to fit in memory.
report = machyon.download("products.parquet", "images/", url_col="image_link", names="{label}/{hash}{ext}")
print(report)
