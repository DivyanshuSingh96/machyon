---
description: The machyon command line, its five commands and every flag, captured from the real --help output.
---

# CLI reference

```text
machyon [download] SOURCE [-o DIR] [options]
machyon retry [-o DIR] [--all] [options]
machyon names SOURCE [--names TEMPLATE] [--limit N | --output FILE]
machyon doctor URL [--referer URL] [--header 'Name: value']
machyon ui [--output-dir DIR] [--port PORT] [--no-browser] [--max-upload-mb MB]
```

`download` is the default, so `machyon urls.csv -o images/` works. The help text below is captured from the
installed version ({{ machyon_version }}) when this site is built.

## `machyon download`

Downloads every URL in `SOURCE` into the output folder (default `images`). Prints a summary, explains each kind of
failure once, and tells you where the ledger is.

{{ cli_help("download") }}

## `machyon retry`

Retries the failures recorded in the output folder's `failed_downloads.jsonl`, under their original filenames.
You don't need the source file. Permanent failures (a 404, a page that isn't an image) are skipped unless you pass
`--all`.

{{ cli_help("retry") }}

## `machyon names`

Shows the URL-to-filename mapping a template produces, without downloading anything. Use it to check a template
before a long run.

{{ cast("names") }}

{{ cli_help("names") }}

## `machyon doctor`

Explains why one URL fails: Python and library versions, the proxy in use, DNS, a request through each TLS trust
source, and a plain-language verdict. [Troubleshooting with the doctor](../troubleshooting/index.md#machyon-doctor)

{{ cli_help("doctor") }}

## `machyon ui`

Opens Machyon in your browser: a local app, served from this computer to this computer only, for pasting links or
uploading a manifest, choosing names and settings, and watching the download. Every run is saved in its own folder
inside the workspace, so the history is still there after a restart. The workspace is the folder you last chose in
the app (**Change folder**), or `~/machyon-downloads` until you choose one. `--output-dir` uses another folder for
that launch only, and leaves the folder chosen in the app as it is.

{{ cli_help("ui") }}

It listens on `127.0.0.1` only, on port 8765, or on a free port if 8765 is taken (a port you choose with `--port`
must be free). The link it prints carries a key for this session; the app answers nothing without it. Stop it with
Ctrl+C: a download that is still running stops cleanly and can be resumed.

Machyon fetches the links from your computer, as the other commands do, so a manifest can name addresses on your own
network, and redirects are followed. Use manifests you trust.

!!! note "A manifest called `ui`"
    `machyon <file>` is short for `machyon download <file>`. For a file literally named `ui`, write
    `machyon download ui`.

## Exit status

| Status | Meaning |
|---|---|
| `0` | The command finished. Failures are recorded in the ledger but don't change the status unless you pass `--strict` |
| `1` | `--strict` was given and at least one row failed (`download`), or is still failing (`retry`) |
| `2` | Invalid arguments, or `machyon ui` can't listen on the `--port` you chose |
