import asyncio

import machyon


async def main() -> None:
    # Two independent downloads in the same event loop, each with its own folder and settings.
    train, test = await asyncio.gather(
        machyon.adownload("train.csv", "images/train", names="{index}{ext}"),
        machyon.adownload("test.csv", "images/test", names="{index}{ext}", max_concurrency=64),
    )
    print("train:", train)
    print("test: ", test)


asyncio.run(main())
