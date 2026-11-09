## What this changes

<!-- One or two sentences: what, and why. Link the issue if there is one: "Fixes #123". -->

## How it was tested

<!-- Machyon's promise is "fast, and accounts for every file". If you touched the engine, fetcher, planner,
     ledger or disk code, add a test in tests/ that injects the failure you're guarding against
     (tests/fakecdn.py can simulate most of them). For speed changes, include before/after numbers from
     `python benchmarks/harness.py`, with the scenario and machine. -->

## Checklist

- [ ] `pytest -q` passes
- [ ] `ruff check src tests tools` passes
- [ ] Docs updated if behaviour changed (`zensical build` and `python tools/check_docs.py` pass)
- [ ] `CHANGELOG.md` has a line under an "Unreleased" heading, for user-visible changes
