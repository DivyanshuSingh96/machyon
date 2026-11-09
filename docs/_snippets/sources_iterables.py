import machyon

with open("urls.txt", encoding="utf-8") as f:
    urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

# 1. A plain list (or any iterable / generator) of URLs
machyon.download(urls, "out/plain")

# 2. (url, filename) pairs: you choose every name
machyon.download([(u, f"{i:05d}.jpg") for i, u in enumerate(urls)], "out/pairs")

# 3. URLs plus a list of names, matched one to one
machyon.download(urls, "out/named", [f"photo_{i}.jpg" for i in range(len(urls))])

# 4. Dicts: every key is a column you can use in a template
rows = [{"image_url": u, "split": "train" if i % 5 else "val"} for i, u in enumerate(urls)]
machyon.download(rows, "out/dicts", names="{split}/{index}{ext}")
