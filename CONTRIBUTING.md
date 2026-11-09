# Contributing to Machyon

Thanks for helping! Machyon's promise is **fast, and accounts for every file**. Every change has to keep both halves of that promise.

## Setup

```bash
git clone https://github.com/DivyanshuSingh96/machyon && cd machyon
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q            # ~2 minutes: real HTTP against a fault-injecting fake CDN
ruff check src tests tools
```

Working on the documentation? `pip install -r docs/requirements.txt`, then `zensical serve` for a live preview.
[How the docs are built and tested](https://divyanshusingh96.github.io/machyon/project/documentation/)

## Ground rules

- **Correctness first.** A change that makes things faster but can lose, corrupt or fake a file won't be merged. If you touch the engine, fetcher, planner or disk code, add a test in `tests/` that injects the failure you're guarding against. `tests/fakecdn.py` can simulate latency, 429/503 errors, truncation, stalls, slow-drip, soft-404s, chunked bodies and Range requests.
- **Claim speed with numbers.** For performance changes, include before/after numbers from `python benchmarks/harness.py` (and say which scenario and machine).
- **Keep the core small.** The runtime dependencies are `aiohttp`, `tqdm`, `truststore` and `certifi`. Anything else goes into an optional extra.
- **Python 3.11+**, type hints on public APIs, `ruff` clean.

## Good first issues

- More input sources (sitemaps, S3/GCS listings) behind optional extras
- Post-download hooks (resize/convert) as an optional module
- Documentation examples for your field (ML datasets, e-commerce, archiving)

## Reporting bugs

Please include the Machyon and Python versions, your OS, a minimal URL list (or the failing host's behaviour), and the relevant lines from `failed_downloads.jsonl`.
