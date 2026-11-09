---
description: The machyon command line, its four commands and every flag, captured from the real --help output.
---

# CLI reference

```text
machyon [download] SOURCE [-o DIR] [options]
machyon retry [-o DIR] [--all] [options]
machyon names SOURCE [--names TEMPLATE] [--limit N | --output FILE]
machyon doctor URL [--referer URL] [--header 'Name: value']
```

`download` is the default, so `machyon urls.csv -o images/` works. The help text below is captured from the
installed version ({{ machyon_version }}) when this site is built.

## `machyon download`

Downloads every URL in `SOURCE` into the output folder (default `images`). Prints a summary, explains each kind of
failure once, and tells you where the ledger is.

```text
{{ cli_help("download") }}
```

## `machyon retry`

Retries the failures recorded in the output folder's `failed_downloads.jsonl`, under their original filenames.
You don't need the source file. Permanent failures (a 404, a page that isn't an image) are skipped unless you pass
`--all`.

```text
{{ cli_help("retry") }}
```

## `machyon names`

Shows the URL-to-filename mapping a template produces, without downloading anything. Use it to check a template
before a long run.

{{ cast("names") }}

```text
{{ cli_help("names") }}
```

## `machyon doctor`

Explains why one URL fails: Python and library versions, the proxy in use, DNS, a request through each TLS trust
source, and a plain-language verdict. [Troubleshooting with the doctor](../troubleshooting/index.md#machyon-doctor)

```text
{{ cli_help("doctor") }}
```

## Exit status

| Status | Meaning |
|---|---|
| `0` | The command finished. Failures are recorded in the ledger but don't change the status unless you pass `--strict` |
| `1` | `--strict` was given and at least one row failed (`download`), or is still failing (`retry`) |
| `2` | Invalid arguments |
