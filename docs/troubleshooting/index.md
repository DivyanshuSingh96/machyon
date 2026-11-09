---
description: Fix the common problems - 403s and HTML pages instead of images, certificate errors, timeouts, rate limits, proxies, Windows file locks - starting with machyon doctor.
---

# Troubleshooting

<p class="lead">Start with what Machyon already told you. Every failed row is in
<code>failed_downloads.jsonl</code> with its reason and the real error, and the CLI explains each kind of failure
once at the end of a run.</p>

## `machyon doctor`

Pick one URL that fails and ask the doctor about it:

{{ cast("doctor") }}

It prints, in order:

1. **Versions:** Machyon, Python, the platform, aiohttp, OpenSSL, and which optional packages are installed.
2. **Proxy:** the proxy Machyon will use for this host, from `HTTPS_PROXY`/`HTTP_PROXY` and your system
   settings, honouring `NO_PROXY`.
3. **DNS:** the addresses the host resolves to.
4. **A real request through each TLS trust source:** your OS store (`native`), Mozilla's bundle (`certifi`),
   Python's defaults (`python`), and, for HTTPS, one request without verification, for diagnosis only. Each line
   shows the status, content type, size, and whether the body is an image.
5. **A verdict** in plain language, with the flag to use.

Pass `--referer` and `--header` to test with the same headers as your run. The doctor exits with status 0 when the
host works (possibly with a different `--tls-trust`) and 1 when it doesn't.

## 403 or not_image on every URL

The server is refusing requests that don't look like they come from its own pages (hotlink protection), wants a
login, or is showing a bot check. The doctor says *"the server didn't return an image"*.

- Send the page the images appear on as the referer: `--referer https://site.example.com/`.
- If the site needs a login, copy the cookie or token from your browser: `--header "Cookie: session=…"`.
- If it's a bot check, slow down (`--max-concurrency 4`) and make sure you're allowed to download from the site
  at all. [Responsible use](../project/responsible-use.md)

## ssl_certificate errors

The certificate couldn't be verified, so nothing was downloaded. The doctor tells you which case you're in:

| Verdict | Cause | Fix |
|---|---|---|
| *Your OS trust store rejects this host but 'certifi' accepts it* | Your OS store is missing a root certificate | `--tls-trust certifi` (or `tls_trust="certifi"`) |
| *Every trust source rejects the certificate, but the server answers without verification* | Something is re-signing HTTPS: antivirus "HTTPS scanning", a corporate proxy, a captive portal, or your clock is wrong | Fix the clock; add the proxy's CA certificate to your OS store; or turn off HTTPS scanning |
| *The host can't be reached at all* | DNS, firewall or the server is down | See the errors above the verdict |

`--insecure` turns verification off. Use it only for a host you trust, and only once you know why verification
fails.

## Timeouts

Many `timeout` failures mean the server or your link is overloaded.

- **Your link is full** (the progress bar's MB/s is flat at your connection speed): lower `--max-concurrency`.
  More connections only make each one slower.
- **The server is slow to respond:** raise `--timeout` (seconds of silence allowed, default 15).
- **Single huge files:** raise `max_request_time` (default 120 s; it already grows with file size).

Then `machyon retry -o images/` picks up whatever timed out.

## Rate limited (http_429)

Machyon already paused the host and lowered concurrency every time it saw a 429. If rows still ended as
`http_429`, the server's limit is stricter than its `Retry-After` suggested. Wait, then:

```bash
machyon retry -o images/ --max-concurrency 8
```

## Behind a proxy

Machyon uses the standard proxy variables, resolved once per host:

```bash
export HTTPS_PROXY=http://proxy.company.example:8080
export NO_PROXY=localhost,.internal.example
```

Run `machyon doctor <url>` to confirm which proxy it picked for a host.

## Windows

- Files briefly locked by antivirus or the search indexer are retried automatically.
- Names that are invalid on Windows (`:`, `?`, `CON`, trailing dots) are made safe automatically, and names that
  differ only in case are treated as the same file.
- `pip install "machyon[fast]"` installs `winloop` for a faster event loop.

## Every row skipped, but files look wrong

Skipping trusts that a non-empty file with the right name is complete. If the folder held files from another
tool, run once with `--verify-existing` to re-download any that don't start with an image signature.

## Still stuck

Open an issue at [{{ config.extra.repo }}/issues]({{ config.extra.repo }}/issues) with:

- the output of `machyon doctor <one failing url>`;
- the Machyon and Python versions and your operating system;
- a few lines from `failed_downloads.jsonl` (remove anything private).
