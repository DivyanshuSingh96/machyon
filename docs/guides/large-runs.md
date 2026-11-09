---
description: Download millions of images - memory use, splitting work across processes or machines, folder layout, and keeping a history of runs.
---

# Large runs

<p class="lead">Machyon is designed for lists of any length on the machine you already have. Here's what changes
when the list has millions of rows.</p>

## Memory

Input is read and planned in shards of 200,000 rows (`shard_size`), so the source never has to fit in memory. To
catch filename conflicts across the *whole* run, Machyon keeps a small fingerprint per row: about 70 bytes, or
roughly 0.7 GB for 10 million rows. For much larger inputs, split the run (below).

## Folder layout

Very large single folders are slow on every filesystem and awkward for every tool. Spread files over
subfolders with a prefix of the hash:

```python
names="{hash:.2}/{hash}{ext}"     # 256 folders: 00/ … ff/
names="{hash:.3}/{hash}{ext}"     # 4,096 folders, for tens of millions of files
```

Or group by something meaningful: `{label}/{hash}{ext}`, `{host}/{hash}{ext}`.

## Splitting the work

Use `start` and `end` to give each process or machine a slice of the same list. Slices can share one folder:
files are written atomically and the [failure ledger](../reliability/failure-ledger.md) is merged under a lock.

```python title="large_runs.py"
--8<-- "large_runs.py"
```

```bash
# four terminals, or four machines with a shared folder
machyon urls.csv -o images/ --start 0       --end 2500000
machyon urls.csv -o images/ --start 2500000 --end 5000000
machyon urls.csv -o images/ --start 5000000 --end 7500000
machyon urls.csv -o images/ --start 7500000
```

`{index}` is the row's position in the whole list, so every slice names files exactly as a single run would.

One process usually saturates a home connection. Split across processes when you're CPU-bound (very small files at
high rates) or want to use several machines or network links.

## Speed on big machines

- `pip install "machyon[fast]"` swaps in uvloop (Linux, macOS) or winloop (Windows). It matters when Python
  itself is the bottleneck: many small images on a fast link.
- `max_concurrency` (256 by default) is also the size of the connection pool. Raising it helps only when many
  hosts or very high-latency links are involved; the adaptive limiter won't use more than helps.
- File writes run on a thread pool (`write_threads`, by default `min(32, 4 × CPUs)`).

## Keeping a record

For long or repeated jobs, keep a history:

```python
machyon.download("urls.csv", "images/", metrics_dir="runs/", log_dir="logs/")
```

Each run appends a summary to `runs/metrics_json_info.json`, and `logs/machyon.log` has a line for every failed
URL. Together with the ledger, you can always answer "what happened, and what's still missing?"

## Checklist for a multi-day job

- Preview names with `machyon names` first; names can't change between runs
- Hash-prefix subfolders for more than ~100,000 files
- `--metrics` and `--log` on
- Run in `tmux`/`screen` or as a service; re-run the same command after any interruption
- `machyon retry` at the end for whatever was transient
- `--strict` in scripts, so a non-zero exit status flags failures
