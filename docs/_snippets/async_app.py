import machyon


async def main() -> None:
    async with machyon.Downloader("images/", max_retries=8) as dl:
        report = await dl.download("urls.csv", names="{host}/{hash}{ext}")
        print(report)
        for failure in dl.failures:        # what is still missing, and why
            print(failure["reason"], failure["url"], "->", failure["name"])


machyon.run(main())   # asyncio.run(), on uvloop/winloop when installed
