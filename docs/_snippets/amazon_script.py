import machyon

# Same file names as the starter kit: images/train/71XfHPR36-L.jpg
opts = dict(url_col="image_link", names="{stem}{ext}")
train = machyon.download("dataset/train.csv", "images/train", **opts)
test = machyon.download("dataset/test.csv", "images/test", **opts)
print(train)
print(test)
