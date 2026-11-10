"""The benchmark fake CDN is the test suite's fake CDN: one implementation, so the two can never drift apart.

tests/fakecdn.py is the single source (it ships in the source distribution with the tests). This file keeps the
benchmark entry points working exactly as before:

    python benchmarks/fakecdn.py --port 8800 ...     # runs the server
    import fakecdn                                   # (with benchmarks/ on sys.path) gives the same module
"""
import importlib.util
import os
import runpy
import sys

_SOURCE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fakecdn.py")

if __name__ == "__main__":
    runpy.run_path(_SOURCE, run_name="__main__")
else:
    _spec = importlib.util.spec_from_file_location(__name__, _SOURCE)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[__name__] = _module  # importers (and `fakecdn.REAL_JPEG = ...`) see the real module
    _spec.loader.exec_module(_module)
