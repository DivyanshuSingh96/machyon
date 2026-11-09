"""Exceptions raised by Machyon."""


class MachyonError(Exception):
    """Base class for Machyon errors."""


class FatalDiskError(MachyonError, RuntimeError):
    """The destination can't be written (disk full, read-only, quota). The run stopped early;
    every unfinished row is recorded in the failure manifest so it can be resumed."""

