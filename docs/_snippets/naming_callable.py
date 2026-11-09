import machyon


def name(url: str, index: int, row: dict | None) -> str:
    """Any logic you like. Return a path relative to the save folder; '/' makes subfolders."""
    label = (row or {}).get("label", "unlabelled")
    return f"{label}/{index:06d}.{{ext}}"    # "{ext}" (in braces) = the real format, from the bytes


report = machyon.download("products.csv", "images/", names=name)
print(report)
