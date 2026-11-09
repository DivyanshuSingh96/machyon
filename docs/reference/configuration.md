---
description: Every DownloadConfig option with its default, its CLI flag and what it does. Generated from the source.
---

# Configuration reference

<p class="lead">The defaults are tuned for a long list of image URLs on an ordinary computer and connection. Most
people change nothing.</p>

Set an option in any of these ways; they all end up in the same [`DownloadConfig`][machyon.DownloadConfig]:

=== "Keyword"

    ```python
    machyon.download("urls.csv", "images/", max_retries=8, referer="https://example.com/")
    ```

=== "DownloadConfig"

    ```python
    cfg = machyon.DownloadConfig(max_retries=8, referer="https://example.com/")
    machyon.download("urls.csv", "images/", config=cfg)
    ```

=== "CLI flag"

    ```bash
    machyon urls.csv -o images/ --retries 8 --referer https://example.com/
    ```

=== "INI file"

    ```ini title="machyon.ini"
    [SETTINGS]
    max_retries = 8
    referer = https://example.com/
    ```

    `DownloadConfig.from_ini("machyon.ini")`, or `machyon --config machyon.ini`. Keys are field names, in any
    case. Dictionary options (`headers`) can't be set from INI files; pass them in code or with `--header`.

A flag shown as `--no-adaptive` turns a `True` default off. Options without a flag are available from Python and
INI files only.

{{ config_table() }}

## Where things are written

| Path | Written when | Contents |
|---|---|---|
| `<save_dir>/failed_downloads.jsonl` | Something failed | [The failure ledger](../reliability/failure-ledger.md) |
| `<save_dir>/failed_downloads.txt` | Something failed | The failed URLs, one per line |
| `<save_dir>/.<name>.machpart` | During a streamed download | A partial body, kept for resuming; removed when finished or abandoned |
| `~/.machyon/profiles.json` | After a run that downloaded something, with `adaptive` and `warm_start` on | The concurrency learned per host |
| `<metrics_dir>/metrics_json_info.json` | `metrics_dir` is set | One summary entry per run |
| `<log_dir>/machyon.log` | `log_dir` is set | A rotating log |
