---
description: How Machyon keeps track of every row, from planning to the file on disk, and where each guarantee stops.
---

# Reliability

<p class="lead">Fast is easy. Fast and complete is the hard part. Machyon is built so that when a run ends, you
know the state of every single row.</p>

Opa, the mascot, has five eyes. Machyon checks every row at five points too:

<div class="mx-five" markdown>

{{ five_eyes() }}

1. **The plan.** Before any request: is the URL usable, is the filename safe and unique, is the file already on
   disk? Duplicate URLs are fetched once.
2. **The response.** A transient error is retried with backoff; a permanent one (a 404, an unusable URL) is
   recorded at once instead of wasting retries. [Retries and rate limits](retries-and-rate-limits.md)
3. **The pace.** A 429 pauses only the host that sent it, for as long as it asked. Concurrency adapts to what the
   server and your connection can take. [Adaptive concurrency](../performance/adaptive-concurrency.md)
4. **The bytes.** The body must start with a real image signature, so an HTML error page is never saved as
   `photo.jpg`. Large bodies are checked for their full length and resume after a dropped connection.
   [Image validation](image-validation.md)
5. **The disk.** A file appears only when it is complete, by an atomic rename. A full disk stops the run cleanly
   instead of producing broken files. [Resuming](resuming.md)

</div>

Whatever happens at any of these points ends up in one of three places: a complete file, a skipped row, or a line
in the [failure ledger](failure-ledger.md). That is [the zero-lost contract](zero-lost.md).

## In this section

| Page | Answers |
|---|---|
| [The zero-lost contract](zero-lost.md) | What exactly is guaranteed, and where the guarantee stops |
| [Retries and rate limits](retries-and-rate-limits.md) | What is retried, how often, how long Machyon waits |
| [Image validation](image-validation.md) | What counts as an image, and what doesn't |
| [Resuming](resuming.md) | What happens when you run the same command again, or after a crash |
| [The failure ledger](failure-ledger.md) | Reading `failed_downloads.jsonl` and acting on it |
