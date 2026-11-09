"""Filenames: templates (``NameTemplate``) and the class-based ``ImageRenamer``.

Template keys:
    {index}  row number in the input (0-based, original position)
    {hash}   16 hex chars of BLAKE2b(url): unique, stable, order-independent
    {stem}   the URL's file name without extension
    {ext}    the URL's image extension, or - if the URL has none - the real format detected
             from the downloaded bytes (".jpg", ".png", ".webp", ...)
    {host}   the URL's host name
    {<column>}  any column of a CSV/JSON/Parquet input
"""
from __future__ import annotations

import csv
import hashlib
import logging
import os
import string
from collections import Counter
from collections.abc import Callable, Iterable, Iterator

from .jobs import AUTO_EXT
from .utils import IMAGE_EXTS, safe_filename, split_host, url_path_parts

logger = logging.getLogger("machyon.naming")

DEFAULT_TEMPLATE = "{hash}{ext}"
BUILTIN_KEYS = {"index", "hash", "stem", "ext", "host"}

# pandas' default NA strings: reproduced so ImageRenamer names stay identical without pandas
_PANDAS_NA = {"", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN",
              "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"}


def generate_hashed_name(value, length: int = 16) -> str:
    """Short deterministic BLAKE2b hex string (not for security)."""
    digest_size = min(64, max(1, (length + 1) // 2))
    return hashlib.blake2b(str(value).encode("utf-8"), digest_size=digest_size).hexdigest()[:length]


def template_keys(template: str) -> set[str]:
    return {f.split(".")[0].split("[")[0] for _, f, _, _ in string.Formatter().parse(template) if f}


class NameTemplate:
    """Compiled filename template. Call with (url, index, row) -> relative filename."""

    def __init__(self, template: str = DEFAULT_TEMPLATE, columns: Iterable[str] | None = None) -> None:
        self.template = template
        self.keys = template_keys(template)
        self._checked = False
        if columns is not None:
            self._check(columns)

    def _check(self, columns: Iterable[str]) -> None:
        unknown = self.keys - BUILTIN_KEYS - set(columns)
        if unknown:
            raise ValueError(f"Unknown name template keys {sorted(unknown)}; "
                             f"available: {sorted(BUILTIN_KEYS | set(columns))}")
        self._checked = True

    def __call__(self, url: str, index: int, row: dict | None = None) -> str:
        if not self._checked:  # columns become known with the first row
            self._check(row.keys() if row else ())
        values = dict(row) if row else {}
        for k in self.keys - BUILTIN_KEYS:  # a column missing or blank in THIS row
            if values.get(k) is None:
                raise KeyError(k)
        keys = self.keys
        if "stem" in keys or "ext" in keys:
            stem, ext = url_path_parts(url)
            values["stem"] = stem or "image"
            values["ext"] = ext if ext in IMAGE_EXTS else AUTO_EXT
        if "hash" in keys:
            values["hash"] = generate_hashed_name(url, 16)
        if "host" in keys:
            values["host"] = split_host(url) or "unknown"
        values["index"] = index
        name = self.template.format_map(values)
        return "/".join(safe_filename(part) for part in name.replace("\\", "/").split("/") if part)


def as_namer(names) -> Callable[[str, int, dict | None], str]:
    """Normalise the many ways a caller can say how to name files."""
    if names is None:
        return NameTemplate()
    if isinstance(names, str):
        return NameTemplate(names)
    if isinstance(names, NameTemplate) or callable(names):
        return names
    raise TypeError("names must be a template string, a callable(url, index, row), or a sequence of names")


# ------------------------------------------------------------------ class-based renamer

class ImageRenamer:
    """Build (urls, filenames) from a CSV with fixed column roles (index, link, group, entity).
    Names are deterministic, so re-running on the same CSV recognises finished files. For free-form
    naming, ``machyon.download(..., names="{template}")`` is simpler."""

    FORMAT_KEYS = {"index", "image_name", "group_id", "entity", "extension"}

    def __init__(self, index_col=None, link_col="image_link", group_col=None, entity_col="entity_name",
                 extension="png", name_format="{index}_{image_name}_{group_id}_{entity}.{extension}"):
        used = template_keys(name_format)
        unknown = used - self.FORMAT_KEYS
        if unknown:
            raise ValueError(f"name_format uses unknown keys {sorted(unknown)}; allowed: {sorted(self.FORMAT_KEYS)}")
        self._fields = used
        self.index_col, self.link_col, self.group_col = index_col, link_col, group_col
        self.entity_col, self.name_format = entity_col, name_format
        self.extension = extension.lstrip(".")

    def _rows(self, csv_path, need_entity: bool, limit=None) -> Iterator[tuple[int, dict]]:
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Error: File '{csv_path}' not found.")
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, [])
            required = [self.link_col] + ([self.entity_col] if need_entity and self.entity_col else [])
            for col in required:
                if col not in header:
                    raise KeyError(f"Error: Column '{col}' not found in the CSV file. Columns: {header}")
            pos = {c: header.index(c) for c in (self.link_col, self.index_col, self.group_col, self.entity_col)
                   if c and c in header}
            skipped = 0
            i = -1
            for raw in reader:
                if not raw:
                    continue  # blank line: pandas skips these without counting them
                i += 1
                if limit is not None and i >= limit:
                    break
                vals = {c: (raw[p] if p < len(raw) else "") for c, p in pos.items()}
                vals = {c: (None if v in _PANDAS_NA else v) for c, v in vals.items()}
                link = vals.get(self.link_col)
                if link is None or not link.strip():
                    skipped += 1
                    continue
                yield i, vals
            if skipped:
                logger.warning(f"Skipped {skipped} rows with no '{self.link_col}' (nothing to download)")

    def process_csv(self, csv_path, limit=None):
        """Readable names from CSV columns. Returns (urls, names)."""
        links, names = [], []
        fmt, ext = self.name_format.format, self.extension
        for i, v in self._rows(csv_path, "entity" in self._fields, limit):
            url = v[self.link_col].strip()
            stem, _ = url_path_parts(url)
            index = v.get(self.index_col) if self.index_col and self.index_col in v else str(i)
            group = v.get(self.group_col) if self.group_col and self.group_col in v else None
            entity = v.get(self.entity_col) if self.entity_col and self.entity_col in v else None
            names.append(safe_filename(fmt(index=index if index is not None else "", image_name=stem or "unknown",
                                           group_id=group if group is not None else "unknown",
                                           entity=entity if entity is not None else "unknown", extension=ext)))
            links.append(url)
        dup = [n for n, c in Counter(names).items() if c > 1]
        if dup:
            logger.warning(f"{len(dup)} filenames repeat (e.g. {dup[:3]}); only the first row for each will be "
                           f"downloaded. Include {{index}} in name_format to make them unique.")
        return links, names

    def hashed_names(self, csv_path, hash_length=16, limit=None):
        """Short unique names ``{row}_{blake2b(entity_row)}.{ext}`` (blank entity hashes as "nan",
        so names never change between runs)."""
        links, names = [], []
        for i, v in self._rows(csv_path, bool(self.entity_col), limit):
            url = v[self.link_col].strip()
            e = v.get(self.entity_col) if self.entity_col else url
            e = "nan" if e is None else e
            links.append(url)
            names.append(f"{i}_{generate_hashed_name(f'{e}_{i}', hash_length)}.{self.extension}")
        return links, names

    async def generate_hashed_names(self, csv_path, hash_length=16, limit=None):
        """Awaitable alias of :meth:`hashed_names`."""
        return self.hashed_names(csv_path, hash_length, limit)
