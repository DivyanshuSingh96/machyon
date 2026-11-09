---
description: How Machyon decides a response really is an image, which formats it recognises, and the limits of that check.
---

# Image validation

<p class="lead">A status of 200 doesn't mean you got an image. Servers answer missing images with HTML "not found"
pages, login walls, bot checks and JSON errors, all with status 200. Machyon looks at the bytes.</p>

## The check

Before anything is written, Machyon reads the first bytes of the body and compares them with known image
signatures ("magic bytes"):

| Format | Starts with | Saved as |
|---|---|---|
| JPEG | `FF D8 FF` | `.jpg` |
| PNG | `89 50 4E 47 0D 0A 1A 0A` | `.png` |
| GIF | `GIF87a` or `GIF89a` | `.gif` |
| WebP | `RIFF` … `WEBP` | `.webp` |
| AVIF, HEIC | an `ftyp` box with `avif`/`avis` or `heic`/`heix`/`mif1`/`msf1` | `.avif`, `.heic` |
| JPEG XL | `FF 0A`, or the JXL container signature | `.jxl` |
| TIFF | `II*␀` or `MM␀*` | `.tiff` |
| BMP | `BM` | `.bmp` |
| ICO | `00 00 01 00` | `.ico` |
| SVG | `<svg` or `<?xml`, **and** a `Content-Type` that mentions `svg` | `.svg` |

The decision:

- **A known signature:** it's an image, whatever the `Content-Type` says. Real images served as
  `application/octet-stream` or `text/plain` are kept.
- **No known signature, but `Content-Type: image/…`, and the body doesn't start like HTML, XML or JSON:** kept.
  This lets new formats through without a Machyon update.
- **Anything else:** rejected as [`not_image`](../reference/failure-reasons.md#not-image), a permanent failure.
  Nothing is written, and the ledger records what the server sent instead, for example
  `Content-Type text/html, body starts b'<html>not found</html>'`.

## Automatic extensions

When a filename uses `{ext}` and the URL has no image extension (`/photo?id=42`, `/api/image/42`), the extension
comes from the signature: a WebP served from a `.jpg`-less URL is saved as `.webp`. When the URL does end in an
image extension, that extension is used as given. [Filenames](../guides/naming.md)

## Complete bodies

The signature tells you what a file is. Completeness is checked separately:

- When the server sends a `Content-Length`, the body must match it. A short body is `truncated` and retried.
- Bodies over `stream_threshold` (4 MiB), or of unknown size, stream to a part file and resume with HTTP range
  requests after a dropped connection. [Resuming](resuming.md#large-files)
- An empty 200 is `empty_body`, and retried.

## Limits

- **Pixels aren't decoded.** Decoding every image would cost far more CPU than downloading it. A file that is
  damaged at the source, but has a valid header and the advertised length, is saved as served. If that matters,
  decode in your pipeline (for example with Pillow's `Image.verify()`) and delete what fails; Machyon re-downloads
  missing files on the next run.
- **No length, no length check.** If the server sends no `Content-Length`, or compresses the response (so the
  length counts compressed bytes), and closes the connection cleanly part-way through, there's nothing to compare
  against.
- **Validation can be turned off.** `validate=False` (`--no-validate`) keeps every body. Use it only for
  downloads that aren't images.
- **Existing files aren't re-checked by default.** See
  [`verify_existing`](../reference/configuration.md#verify-existing).
