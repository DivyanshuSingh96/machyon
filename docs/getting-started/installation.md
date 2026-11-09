---
description: Install Machyon with pip, add the optional extras for Parquet or a faster event loop, and check the install.
---

# Installation

Machyon needs **Python 3.11 or newer** and runs on Linux, macOS and Windows.

```bash
pip install machyon
```

That installs the library and the `machyon` command. It brings four dependencies:

| Package | Why |
|---|---|
| `aiohttp` | The asynchronous HTTP client |
| `tqdm` | The progress bar |
| `truststore` | Verifies HTTPS with your operating system's certificate store, like your browser does |
| `certifi` | Mozilla's certificate bundle, as a fallback (`tls_trust="certifi"`) |

## Optional extras

| Install | Adds | When you want it |
|---|---|---|
| `pip install "machyon[parquet]"` | `pyarrow` | Reading `.parquet` files |
| `pip install "machyon[fast]"` | `uvloop` on Linux and macOS, `winloop` on Windows | A faster event loop for very large, CPU-bound runs. `machyon.download()` and the CLI use it automatically; in your own code, call `machyon.run(main())` instead of `asyncio.run(main())` |
| `pip install "machyon[all]"` | Both of the above | |

Reading pandas or polars DataFrames needs no extra: Machyon uses whichever one you pass it.

## Check the install

```bash
machyon --version
```

To check that your machine can reach a particular server (DNS, proxy, HTTPS trust, and what the server sends
back), point the doctor at one image URL:

```bash
machyon doctor "https://example.com/some/image.jpg"
```

[What the doctor checks](../troubleshooting/index.md#machyon-doctor)

## Next

[Quickstart: your first download](quickstart.md)
