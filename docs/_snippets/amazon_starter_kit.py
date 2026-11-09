import asyncio
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import machyon


def download_images(image_links, download_folder, allow_multiprocessing=True):
    """Drop-in for the starter kit's version: same arguments, same file names.
    Works in scripts and in notebooks (src/test.ipynb calls it from a notebook)."""
    links = [str(u) for u in image_links]

    def job():
        return machyon.download(links, download_folder, names="{stem}{ext}")

    try:
        asyncio.get_running_loop()       # inside a notebook: an event loop is running
    except RuntimeError:
        return job()
    with ThreadPoolExecutor(1) as pool:  # so download on a worker thread instead
        return pool.submit(job).result()


train = pd.read_csv("dataset/train.csv")
print(download_images(train["image_link"], "images/train"))
