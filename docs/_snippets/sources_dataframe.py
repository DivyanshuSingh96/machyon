# needs: pip install pandas   (polars works the same way)
import pandas as pd

import machyon

df = pd.read_csv("products.csv")
df = df[df["label"] != "birds"]                  # filter with pandas first
report = machyon.download(df, "images/", url_col="image_link", names="{label}/{index}{ext}")
print(report)
