from pathlib import Path

import pandas as pd

train = pd.read_csv("dataset/train.csv")
names = [Path(u).name for u in train["image_link"]]
train["image_path"] = [f"images/train/{n}" for n in names]
train["has_image"] = [Path(p).is_file() for p in train["image_path"]]
print(train["has_image"].value_counts())  # False? use text features
