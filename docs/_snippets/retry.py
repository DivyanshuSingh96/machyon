import machyon


async def main() -> None:
    async with machyon.Downloader("images/") as dl:
        report = await dl.download("urls.csv", retry_failures=False)   # skip the automatic sweep
        print(report)
        again = await dl.retry_failed()            # transient failures only, same filenames
        print(again or "nothing transient to retry")
        print(dl.failure_reasons())                # what is left, by reason


machyon.run(main())
