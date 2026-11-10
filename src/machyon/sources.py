"""Read image URLs from anywhere: CSV/TSV, TXT, JSON, JSONL, Parquet, DataFrames, iterables.

Everything streams row by row, so a 100-million-line file never has to fit in memory.
"""
from __future__ import annotations

import csv
import itertools
import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
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


FORMATS = {".csv": "csv", ".tsv": "tsv", ".jsonl": "jsonl", ".ndjson": "jsonl", ".json": "json", ".parquet": "parquet"}


def _format(path: Path) -> str:
    return FORMATS.get(path.suffix.lower(), "txt")   # anything else is read as one URL per line


def _records(path: Path) -> Iterator:
    """The raw records of a file, before any URL column is chosen: dicts (tables, JSON objects), URL strings
    (TXT, a CSV whose first line is already a URL) or [url, name] pairs (JSON). ``.json`` is parsed whole;
    every other format streams."""
    if not path.exists():
        raise FileNotFoundError(path)
    fmt = _format(path)
    if fmt in ("csv", "tsv"):
        with open(path, newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            f.seek(0)
            delimiter = "\t" if fmt == "tsv" else ("\t" if sample.count("\t") > sample.count(",") else ",")
            reader = csv.reader(f, delimiter=delimiter)
            header = next(reader, None)
            if header is None:
                return
            if any(_looks_like_url(c) for c in header):  # no header row: the first line is data
                for row in _chain(header, reader):
                    if row and any(row):
                        yield next((c for c in row if _looks_like_url(c)), row[0]).strip()
                return
            n = len(header)
            yield from (dict(zip(header, row + [None] * (n - len(row)), strict=False)) for row in reader if any(row))
    elif fmt == "jsonl":
        with open(path, encoding="utf-8") as f:
            yield from (json.loads(line) for line in f if line.strip())
    elif fmt == "json":
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
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of URLs or objects, or an object holding one")
        yield from data
    elif fmt == "parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise ImportError("Reading Parquet needs pyarrow: pip install 'machyon[parquet]'") from e
        pf = pq.ParquetFile(path)
        for b in pf.iter_batches(batch_size=65536):
            yield from b.to_pylist()
    else:  # .txt or anything else: one URL per line, '#' comments allowed
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    yield line


def _read_file(path: Path, url_col: str | None) -> Iterator[Row]:
    yield from read_rows(_records(path), url_col)


# ---------------------------------------------------------------------- inspecting a source


@dataclass(frozen=True)
class SourceInspection:
    """A look at the first rows of a source file: what ``download()`` would read from it.

    format: ``"csv"``, ``"tsv"``, ``"txt"``, ``"json"``, ``"jsonl"`` or ``"parquet"`` (from the file extension;
        anything unrecognised is read as one URL per line, like ``download()`` does).
    columns: the column names, for tables and lists of objects; ``None`` for a plain list of URLs (a TXT file, a
        CSV whose first line is already a URL, a JSON list of URLs or of ``[url, name]`` pairs).
    url_col: the column ``download()`` would take the URLs from: the one you named, or the guessed one; ``None``
        when there are no columns, or when it can't be guessed (see ``error``).
    url_col_guessed: ``True`` when ``url_col`` was guessed rather than named.
    rows: up to ``limit`` rows as dicts, in file order. A plain list of URLs gives ``{"url": ...}`` (and
        ``"name"`` for pairs).
    urls: the URL of each of those rows (``""`` where a row has none).
    more: ``True`` if the source has more rows than ``limit``. Nothing here counts the rows in the whole source.
    error: ``None``, or what went wrong, as ``(kind, message)``. Kinds: ``"not_found"``, ``"empty"``,
        ``"unreadable"`` (malformed or not text), ``"missing_dependency"``, ``"no_url_column"``,
        ``"unknown_column"``. With ``"no_url_column"`` and ``"unknown_column"``, ``columns`` and ``rows`` are still
        filled in, so a caller can let the user pick the column.
    """

    format: str
    columns: list[str] | None = None
    url_col: str | None = None
    url_col_guessed: bool = False
    rows: list[dict] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    more: bool = False
    error: tuple[str, str] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def inspect(path: str | os.PathLike, url_col: str | None = None, limit: int = 20) -> SourceInspection:
    """Read the first ``limit`` rows of a source file the way ``download()`` reads them, without reading the rest.

    The same reader as ``download()`` is used, so the columns, the guessed URL column and the URLs are the ones a
    download would use. Plain ``.json`` files are parsed whole (as ``download()`` does); every other format stops
    after ``limit + 1`` rows. Never raises for a problem with the file: see ``SourceInspection.error``.
    """
    path = Path(path)
    fmt = _format(path)
    limit = max(0, int(limit))
    try:
        it = iter(_records(path))
        head = list(itertools.islice(it, limit + 1))
    except FileNotFoundError:
        return SourceInspection(fmt, error=("not_found", f"No such file: {path}"))
    except ImportError as e:
        return SourceInspection(fmt, error=("missing_dependency", str(e)))
    except (UnicodeDecodeError, csv.Error, ValueError, OSError, TypeError) as e:
        return SourceInspection(fmt, error=("unreadable", f"{type(e).__name__}: {e}"))
    except Exception as e:  # pyarrow and friends raise their own types for a broken file
        return SourceInspection(fmt, error=("unreadable", f"{type(e).__name__}: {e}"))
    more = len(head) > limit
    head = head[:limit]
    if not head:
        return SourceInspection(fmt, error=("empty", "The source has no rows."))
    first = head[0]
    if isinstance(first, dict):
        columns = [str(c) for c in first.keys()]
        rows = [dict(r) if isinstance(r, dict) else {"value": r} for r in head]
        guessed = url_col is None
        if url_col is not None and url_col not in first:
            return SourceInspection(fmt, columns, None, False, rows, [], more,
                                    ("unknown_column", f"Column '{url_col}' not found. Columns: {columns}"))
        try:
            col = url_col or _guess_url_col(list(first.keys()), first)
        except ValueError as e:
            return SourceInspection(fmt, columns, None, False, rows, [], more, ("no_url_column", str(e)))
        urls = ["" if r.get(col) is None else str(r.get(col)).strip() for r in rows]
        return SourceInspection(fmt, columns, col, guessed, rows, urls, more)
    rows, urls = [], []
    for item in head:
        if isinstance(item, (list, tuple)):
            url = "" if not item else str(item[0]).strip()
            row = {"url": url}
            if len(item) > 1 and item[1] is not None:
                row["name"] = item[1]
        else:
            url = "" if item is None else str(item).strip()
            row = {"url": url}
        rows.append(row)
        urls.append(url)
    return SourceInspection(fmt, None, None, False, rows, urls, more)


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
