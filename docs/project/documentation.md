---
description: How this documentation is built, what is generated from the source, how examples are tested, and how to contribute a page.
---

# About these docs

<p class="lead">Documentation that drifts from the code is worse than none. Most of the facts on this site are
generated from the source or checked against it on every change.</p>

## What is generated

| Content | Comes from |
|---|---|
| [Python API](../reference/python-api.md) | The source and its docstrings, via mkdocstrings |
| [Configuration tables](../reference/configuration.md) | The `DownloadConfig` dataclass: names, defaults and docstrings |
| [CLI reference](../reference/cli.md) | The real `--help` output, captured at build time |
| [Failure reasons](../reference/failure-reasons.md) | A curated table, checked against every reason the code can record; permanence computed by the library itself |
| [Benchmarks](../performance/benchmarks.md) | `benchmarks/results-*.jsonl`, the harness's own output |
| Terminal recordings | Real runs of the CLI against the test CDN (`tools/record_casts.py`) |
| [Simulator](../performance/adaptive-concurrency.md#try-it) | A port of `limiter.py`, compared decision by decision with the Python on every test run |
| [`llms.txt`]({{ config.site_url }}llms.txt) | This site, as plain text for language models and tools |

## What is tested

`tests/test_docs.py` runs with the rest of the test suite:

- every Python example on this site (the ones with a file name above them, kept in `docs/_snippets/`) runs as a
  script against the fault-injecting test CDN, and every failure it records must carry a reason;
- the accounting identity on [the zero-lost page](../reliability/zero-lost.md) is checked on a first run and a
  resume;
- every CLI flag the configuration reference names exists in the parser;
- every failure reason the code can produce is explained, and none that it can't;
- the simulator and the Python limiter make identical decisions;
- every included example exists, and every example is used.

Short fragments without a file name are illustrations; they use real names and signatures, which the strict build
checks for API links.

## Building locally

```bash
pip install -e ".[dev]" -r docs/requirements.txt
zensical serve            # live preview at http://localhost:8000
zensical build            # strict: any broken link or reference fails the build
python tools/check_docs.py
```

Before a release, `python tools/check_docs.py --release` also fails while any placeholder (such as the repository
URL) is left in the configuration or the pages.

## Writing a page

- Start from the reader's task, and give them a working example early.
- State a guarantee with its boundary on the same page.
- Put runnable examples in `docs/_snippets/` and include them with a title; they'll be tested automatically.
- Use reference links like `` [`Report`][machyon.Report] `` for API names; the build checks they resolve.
- Opa appears on the home page, the reliability overview, the 404 page and [Meet Opa](meet-opa.md). That's
  enough Opa.
