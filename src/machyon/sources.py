"""Read image URLs from anywhere: CSV/TSV, TXT, JSON, JSONL, Parquet, DataFrames, iterables.

Everything streams row by row, so a 100-million-line file never has to fit in memory.
"""
from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

URL_COLUMN_GUESSES = ("url", "image_url", "image_link", "img_url", "link", "src", "image", "href", "uri")

Row = tuple[str, dict | None]   # (url, other columns or None)
PAIR_NAME = "\x00name"            # row key used for (url, filename) pairs


def _looks_like_url(v) -> bool:
    return isinstance(v, str) and v.strip().startswith(("http://", "https://"))


def _guess_url_col(columns: list[str], first: dict | None) -> str:
    lower = {c.lower(): c for c in columns}
    for g in URL_COLUMN_GUESSES:
        if g in lower:
            return lower[g]
    if first:
        for c in columns:
            v = first.get(c)
            if isinstance(v, str) and v.strip().startswith(("http://", "https://")):
                return c
    raise ValueError(f"Can't tell which column holds the URLs; pass url_col=. Columns: {columns}")


def _dict_rows(rows: Iterator[dict], url_col: str | None) -> Iterator[Row]:
    first = next(rows, None)
    if first is None:
        return
    col = url_col or _guess_url_col(list(first.keys()), first)
    if col not in first:
        raise KeyError(f"Column '{col}' not found. Columns: {list(first.keys())}")
    for row in _chain(first, rows):
        v = row.get(col)
        yield ("" if v is None else str(v).strip()), row


def read_rows(source, url_col: str | None = None) -> Iterator[Row]:
    """Yield (url, row) from a file path, a DataFrame, or an iterable.

    Iterables may contain URL strings, (url, name) pairs, or dicts. For pairs the row is
    ``{"name": name}``, which ``download()`` uses as the filename.
    """
    if isinstance(source, (str, os.PathLike)) and not str(source).startswith(("http://", "https://")):
        yield from _read_file(Path(source), url_col)
        return
    if isinstance(source, str):  # a single URL
        yield source.strip(), None
        return
    if hasattr(source, "to_dict") and hasattr(source, "columns"):  # pandas / polars DataFrame
        records = source.to_dict("records") if "pandas" in type(source).__module__ else source.to_dicts()
        yield from _dict_rows(iter(records), url_col)
        return
    it = iter(source)
    first = next(it, None)
    if first is None:
        return
    if isinstance(first, dict):
        yield from _dict_rows(_chain(first, it), url_col)
    elif isinstance(first, (tuple, list)):
        for item in _chain(first, it):
            if not item:
                continue
            yield str(item[0]).strip(), ({PAIR_NAME: item[1]} if len(item) > 1 and item[1] is not None else None)
    else:
        for item in _chain(first, it):
            yield ("" if item is None else str(item).strip()), None


def _chain(first, rest):
    yield first
    yield from rest


def _read_file(path: Path, url_col: str | None) -> Iterator[Row]:
    if not path.exists():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = "\t" if suffix == ".tsv" else ("\t" if sample.count("\t") > sample.count(",") else ",")
            reader = csv.reader(f, delimiter=delimiter)
            header = next(reader, None)
            if header is None:
                return
            if any(_looks_like_url(c) for c in header):  # no header row: the first line is data
                for row in _chain(header, reader):
                    url = next((c for c in row if _looks_like_url(c)), row[0] if row else "")
                    if row and any(row):
                        yield url.strip(), None
                return
            n = len(header)
            dict_rows = (dict(zip(header, row + [None] * (n - len(row)), strict=False)) for row in reader if any(row))
            yield from _dict_rows(dict_rows, url_col)
    elif suffix in (".jsonl", ".ndjson"):
        with open(path, encoding="utf-8") as f:
            items = (json.loads(line) for line in f if line.strip())
            yield from read_rows(items, url_col)
    elif suffix == ".json":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):  # {"images": [...]} or a single {"url": ...}
            def holds_rows(v):
                return isinstance(v, list) and v and (_looks_like_url(v[0]) or (
                    isinstance(v[0], dict) and any(_looks_like_url(x) for x in v[0].values())))
            lists = [k for k, v in data.items() if holds_rows(v)]
            if len(lists) > 1:
                raise ValueError(f"JSON object has several URL lists {lists}; pass the list itself")
            data = data[lists[0]] if lists else [data]
        yield from read_rows(data, url_col)
    elif suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise ImportError("Reading Parquet needs pyarrow: pip install 'machyon[parquet]'") from e
        pf = pq.ParquetFile(path)

        def batches() -> Iterator[dict]:
            for b in pf.iter_batches(batch_size=65536):
                yield from b.to_pylist()

        yield from _dict_rows(batches(), url_col)
    else:  # .txt or anything else: one URL per line, '#' comments allowed
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    yield line, None


class BadName(str):
    """A row whose filename couldn't be built; the planner records it as failed (``bad_name``)."""


def iter_pairs(rows: Iterable[Row], namer, name_col: str | None = None, start_index: int = 0) -> Iterator[tuple[str, str]]:
    """(url, filename) pairs from (url, row) rows. Rows that can't be named are yielded as BadName."""
    for i, (url, row) in enumerate(rows, start_index):
        try:
            if row and PAIR_NAME in row:
                yield url, str(row[PAIR_NAME])
            elif name_col:
                v = row.get(name_col) if row else None
                if v is None or str(v).strip() == "":
                    raise KeyError(name_col)
                yield url, str(v)
            else:
                yield url, namer(url, i, row)
        except (KeyError, IndexError, ValueError, TypeError) as e:
            if isinstance(e, ValueError) and "Unknown name template keys" in str(e):
                raise  # a typo in the template: stop early, it would fail every row
            yield url, BadName(f"row {i}: cannot build filename ({type(e).__name__}: {e})")
