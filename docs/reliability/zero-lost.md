---
description: Machyon's core guarantee, stated precisely - every row is written, skipped or recorded with a reason - with its exact boundaries and how it is tested.
---

# The zero-lost contract

<p class="lead">"Zero lost" is a precise promise, not a slogan. This page states it, says where it stops, and
shows how it is checked.</p>

## The contract

**Every input row ends in exactly one of three states:**

| State | Means | You'll find it |
|---|---|---|
| **Written** | A complete, validated image is on disk under the row's filename | in the folder; counted in `report.downloaded` |
| **Skipped** | A complete file was already there, so nothing was downloaded | in the folder; counted in `report.skipped` |
| **Recorded** | The row couldn't be completed. Its URL, filename and reason are in the ledger | in [`failed_downloads.jsonl`](failure-ledger.md); counted in `report.failed` |

So for every call, the numbers add up:

```text
report.total == report.downloaded + report.skipped + report.failed
```

No row is silently dropped: not a timeout, not a rate limit, not a duplicate name, not an HTML page served
with status 200.

Three more promises make the contract useful in practice:

**A file that exists is complete.**
:   Bodies are written to a temporary file and moved into place with an atomic rename. A crash, a Ctrl+C or a
    power cut can leave a temporary file behind (Machyon cleans those up on the next run) but never a
    half-written image under the real name.

**Re-running is resuming.**
:   Complete files are skipped, so running the same command again finishes the job. Failures recorded by earlier
    runs, other slices or other processes are merged, never overwritten. [Resuming](resuming.md)

**Polite under pressure.**
:   `Retry-After` is honoured, a 429 pauses the host that sent it, and concurrency backs off automatically. Being
    reliable never means hammering a server. [Retries and rate limits](retries-and-rate-limits.md)

## Where it stops

A guarantee is only worth something if its edges are clear.

- **Machyon can't download what the server doesn't have.** A dead link stays dead. What Machyon promises is
  that you'll *know*: it's in the ledger as `http_404`, not missing without a trace.
- **"Validated" means the bytes start like an image.** Machyon checks the file signature (the magic bytes) and,
  when the server sends a length, that every byte arrived. It doesn't decode pixels, so a file that is damaged
  *at the source* but has a correct header and length is saved as served.
  [Image validation](image-validation.md)
- **Skipping trusts what's on disk.** A non-empty file with the right name counts as complete. If something else
  might have put files there, set [`verify_existing=True`](../reference/configuration.md#verify-existing) to also
  check their signature before skipping them.
- **Interruptions are recorded for the rows Machyon has started.** Rows are read in shards of 200,000. On Ctrl+C
  or a full disk, every row of the current shard that wasn't finished is recorded as `interrupted`; rows in
  later shards haven't been read yet, so they aren't in the ledger. Either way, running the same command again
  picks up every one of them.
- **A power cut is covered only with `fsync`.** By default, a rename can reach the disk before the file's data
  does, which matters only if the machine loses power. [`fsync=True`](../reference/configuration.md#fsync)
  flushes each file first: slower, but safe against power loss.
- **The validation switch is yours.** With `validate=False` (`--no-validate`), any body is kept, HTML included.
- **Your own rows are your own.** If you pass a list of names, Machyon names files exactly as told. Two rows asking
  for the same name with different URLs are a conflict: the first keeps the name and the other is recorded as
  `duplicate_name`, never overwritten.

## How it's tested

The contract is enforced by tests, not by hope. Machyon's test suite runs real downloads against a local
fault-injecting CDN (`tests/fakecdn.py`) that serves slow tails, 503s, 429s, connections dropped mid-body,
stalls, dead links, HTML "soft 404" pages, images with the wrong content type, redirects, missing lengths, and
servers with and without resume support. Every saved file is compared byte for byte with the original, and every
row is accounted for.

The [benchmarks](../performance/benchmarks.md) report the same three numbers for every tool they measure:
**missing** (a row that could have succeeded but has no file), **corrupt** (a file whose bytes are wrong) and
**bogus** (a file that isn't the image at all). For Machyon, every scenario must show zero in all three.

The examples in this documentation run against the same CDN on every change, and assert that every ledger entry
they produce carries a reason.

The contract is also written down language-neutrally in the [architecture document](../ARCHITECTURE.md), so any
future port must pass the same fault-injection suite to call itself Machyon.
