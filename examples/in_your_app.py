"""Embedding Machyon in an async application (a web service, a crawler, a training pipeline).

Machyon is a library first: one Downloader per destination, reused across calls, inside
your own event loop. Nothing here blocks the loop.
"""
import asyncio

import machyon


async def ingest_batches(batches, out_dir="images/ingest"):
    # One Downloader = one connection pool + one adaptive limiter, shared by every batch.
    # Concurrency learned on batch 1 carries over to batch 2 (and to the next run, per host).
    async with machyon.Downloader(out_dir, progress=False, headers={"Authorization": "Bearer <token>"}) as dl:
        for i, urls in enumerate(batches):
            report = await dl.download(urls, names="{host}/{hash}{ext}")
            print(f"batch {i}: {report}")
        # anything that failed transiently in any batch, retried under its original name
        await dl.retry_failed()
        return dl.failures  # [{"url", "name", "reason"}, ...]


async def main():
    batches = [
        [f"https://picsum.photos/id/{i}/400/300" for i in range(0, 50)],
        [f"https://picsum.photos/id/{i}/400/300" for i in range(50, 100)],
    ]
    failures = await ingest_batches(batches)
    print("still failing:", failures)


if __name__ == "__main__":
    asyncio.run(main())  # or machyon.run(main()) to use uvloop/winloop when installed
