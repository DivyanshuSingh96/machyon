!pip install -q machyon
import machyon

# Same file names as the starter kit: images/train/71XfHPR36-L.jpg
opts = dict(url_col="image_link", names="{stem}{ext}")
train = await machyon.adownload("dataset/train.csv", "images/train", **opts)
test = await machyon.adownload("dataset/test.csv", "images/test", **opts)
print(train)
print(test)
