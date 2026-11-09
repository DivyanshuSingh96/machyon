---
description: Every input Machyon reads - CSV, TSV, TXT, JSON, JSONL, Parquet, DataFrames and Python iterables - and how it finds the URL column.
---

# Input sources

<p class="lead">Pass Machyon whatever holds your URLs. Everything is read row by row, so a file with a hundred
million lines never has to fit in memory.</p>

| Source | Pass | Notes |
|---|---|---|
| CSV | `"urls.csv"` | Header row expected; a file whose first line is already a URL is read as headerless. Comma or tab, detected |
| TSV | `"urls.tsv"` | Tab-separated, otherwise as CSV |
| Text | `"urls.txt"` (or any other extension) | One URL per line. Blank lines and lines starting with `#` are ignored |
| JSON Lines | `"urls.jsonl"`, `"urls.ndjson"` | One object (or URL string) per line |
| JSON | `"urls.json"` | A list of URLs or objects, or an object holding exactly one such list (`{"images": [...]}`) |
| Parquet | `"urls.parquet"` | Read in batches of 65,536 rows. Needs `pip install "machyon[parquet]"` |
| DataFrame | a pandas or polars `DataFrame` | Every column is available to name templates |
| Iterable | a list, tuple or generator | Of URL strings, `(url, filename)` pairs, or dicts |
| One URL | `"https://…/a.jpg"` | Downloads that one image |

## Finding the URL column

For tables (CSV, TSV, JSON objects, Parquet, DataFrames, dicts), Machyon looks for a column named, in order of
preference: `url`, `image_url`, `image_link`, `img_url`, `link`, `src`, `image`, `href`, `uri` (in any case). If
none of those exists, it picks the first column whose value in the first row starts with `http://` or
`https://`. If that fails too, it stops with an error listing the columns.

Name it explicitly whenever you know it:

```python
machyon.download("train.csv", "images/", url_col="image_link")
```

```bash
machyon train.csv -o images/ --url-col image_link
```

Values are stripped of surrounding whitespace. A row whose URL is empty or isn't `http(s)://` with a host is
recorded as [`invalid_url`](../reference/failure-reasons.md#invalid-url), so it's never silently skipped.

## Python iterables

```python title="sources_iterables.py"
--8<-- "sources_iterables.py"
```

Machyon decides how to read an iterable from its **first item**: a string means a list of URLs, a tuple or list
means `(url, filename)` pairs, a dict means rows with columns. Keep all items the same kind.

A generator is consumed lazily, in shards, so you can stream URLs from a database cursor or an API without
building a list. (The progress bar can't show a total for a generator.)

## DataFrames

```python title="sources_dataframe.py"
--8<-- "sources_dataframe.py"
```

Filter, sample or join with pandas or polars first, then hand the result to Machyon. Neither library is a
dependency of Machyon; whichever one you pass is used.

## Parquet

```python title="sources_parquet.py"
--8<-- "sources_parquet.py"
```

## Ready-made filenames

If your table already has a filename column, use it instead of a template:

```python
machyon.download("train.csv", "images/", url_col="image_link", name_col="file_name")
```

```bash
machyon train.csv -o images/ --url-col image_link --name-col file_name
```

A row whose filename cell is blank is recorded as
[`bad_name`](../reference/failure-reasons.md#bad-name).

## Part of a source

`start` and `end` select rows `[start, end)` of any source, counted from 0 in input order:

```python
machyon.download("urls.csv", "images/", start=100_000, end=200_000)
```

```bash
machyon urls.csv -o images/ --start 100000 --end 200000
```

`{index}` in a name template keeps the row's original position, so slices produce the same names as a full run.
[Large runs](large-runs.md)
