---
description: Download only what you may use, respect site terms and robots.txt, identify your crawler, and keep load reasonable. How Machyon helps.
---

# Responsible use

<p class="lead">Machyon is fast. Please point it only at images you're allowed to download, and at servers that are
happy to serve them at that rate.</p>

## Your responsibilities

- **Rights.** Downloading an image doesn't give you the right to use it. Check the licence and the site's terms,
  especially before training models on images or redistributing them.
- **Terms and robots.txt.** Respect each site's terms of service and its `robots.txt`. Machyon downloads URLs you
  give it; it doesn't crawl and doesn't read `robots.txt` for you.
- **Personal data.** Images of people can be personal data under laws such as the GDPR. Collect only what you need
  and handle it accordingly.
- **Identify yourself.** For research or bulk collection, set a `user_agent` that names your project and gives a
  contact address, so operators can reach you instead of blocking you:
  `user_agent="my-lab-dataset/1.0 (+mailto:me@uni.edu)"`.
- **Keep load reasonable.** On servers that aren't built for bulk traffic, cap `max_concurrency` (for example 4 to
  16), and prefer off-peak hours for large jobs.

## What Machyon does for you

- Honours `Retry-After`: after a 429, it sends nothing new to that host until the wait is over. Requests already on
  their way when the 429 arrived still finish.
- Lowers its concurrency automatically on rate limits and error bursts, and remembers a ceiling.
- Fetches a URL that appears on several rows only once.
- Never retries permanent failures automatically, so dead links cost one request, not six.
- Starts each run at the concurrency it learned last time, instead of probing a host's limits again.

[Retries and rate limits](../reliability/retries-and-rate-limits.md)

## If you operate a server

If Machyon traffic is causing you trouble, the `User-Agent` should identify whoever runs it; contact them, or
answer with `429` and a `Retry-After` header: Machyon will slow down and wait as asked. You can also
[open an issue]({{ config.extra.repo }}/issues) if a client is misbehaving.
