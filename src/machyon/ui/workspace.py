"""Where ``machyon ui`` keeps its runs (the workspace), how a folder is checked before it becomes one, the small
per-user file that remembers the folder last chosen in the app, and the staging folder that holds uploaded
manifests until a download starts.

The workspace is decided once at launch:

1. ``--output-dir``, for that launch only (the remembered choice is left as it is);
2. otherwise the folder last chosen in the app, if it is available;
3. otherwise ``~/machyon-downloads``. When a remembered folder is unavailable (an unplugged drive, say), that is
   a *fallback*: the page says so, the remembered choice is kept for next time, and nothing is downloaded there
   until the user has confirmed it.
"""
from __future__ import annotations

import errno
import json
import os
import secrets
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath

from .runs import RUN_ID, write_json_atomic

CONFIG_SCHEMA = 1
CONFIG_NAME = "ui.json"
DEFAULT_WORKSPACE = "~/machyon-downloads"
LOCK_NAME = ".machyon-ui.lock"
PATH_MAX = 4096
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
                 "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
_WIN_BAD = set('<>:"|?*')


class RootLock:
    """One server per folder: held for as long as a ``machyon ui`` uses that folder (its workspace, or its staging
    folder). The operating system releases it if the process ends without letting go."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / LOCK_NAME
        self.f = open(self.path, "a+b")  # noqa: SIM115  held for the server's lifetime
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise

    def release(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.f.close()


# ------------------------------------------------------------------ the remembered choice

def config_dir() -> Path:
    """This user's settings folder for Machyon: %APPDATA%\\Machyon, ~/Library/Application Support/Machyon, or
    $XDG_CONFIG_HOME/machyon (~/.config/machyon)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) / "Machyon" if base else Path.home() / "AppData" / "Roaming" / "Machyon"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Machyon"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg and os.path.isabs(xdg) else Path.home() / ".config") / "machyon"


@dataclass
class Prefs:
    """``ui.json``: the schema version, the workspace last chosen in the app, and room for UI preferences. Holds
    no images, manifests or reports. A file this version can't read (damaged, or written by a newer Machyon) is
    never overwritten: ``problem`` says why, and choices are then used for the session without being remembered."""

    path: Path
    data: dict = field(default_factory=lambda: {"schema": CONFIG_SCHEMA, "workspace": None, "prefs": {}})
    problem: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> Prefs:
        p = Path(path) if path is not None else config_dir() / CONFIG_NAME
        try:
            raw = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(p)
        except OSError as e:
            return cls(p, problem=f"{p} can't be read ({e.strerror or e}), so your choices aren't remembered")
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return cls(p, problem=f"{p} is damaged, so your choices aren't remembered. Fix or delete it")
        schema = data.get("schema")
        if isinstance(schema, int) and not isinstance(schema, bool) and schema > CONFIG_SCHEMA:
            return cls(p, problem=f"{p} was written by a newer Machyon, so this version leaves it as it is and "
                                  "doesn't remember your choices")
        if schema != CONFIG_SCHEMA or not isinstance(data.get("workspace"), (str, type(None))) \
                or not isinstance(data.get("prefs", {}), dict):
            return cls(p, problem=f"{p} is damaged, so your choices aren't remembered. Fix or delete it")
        data.setdefault("prefs", {})
        return cls(p, data)

    @property
    def workspace(self) -> str | None:
        return None if self.problem else self.data.get("workspace")

    def remember_workspace(self, folder: Path) -> str | None:
        """Save ``folder`` as the workspace to use next time. Returns why it couldn't be saved, or ``None``."""
        if self.problem:
            return self.problem
        data = {**self.data, "schema": CONFIG_SCHEMA, "workspace": str(folder)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self.path, data)
        except OSError as e:
            return f"{self.path} couldn't be written ({e.strerror or e}), so this choice isn't remembered"
        self.data = data
        return None


# ------------------------------------------------------------------ checking a folder

class FolderProblem(ValueError):
    """Why a folder can't be the workspace, in words for the person who chose it."""


def windows_name_problem(path: str) -> str | None:
    """What Windows forbids in ``path`` (an absolute Windows path), or ``None``."""
    pure = PureWindowsPath(path)
    for part in pure.parts[1:]:
        if any(c in _WIN_BAD or ord(c) < 32 for c in part):
            return f'"{part}": Windows doesn\'t allow < > : " | ? * or control characters in a folder name.'
        if part.endswith((" ", ".")):
            return f'"{part}": Windows doesn\'t allow a folder name that ends with a space or a dot.'
        if part.split(".")[0].rstrip(" ").upper() in _WIN_RESERVED:
            return f'"{part}": Windows reserves this name for a device; choose another.'
    return None


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":   # Explorer's "Copy as path" adds quotes
        raw = raw[1:-1].strip()
    return raw


def check_folder(raw, *, forbidden: tuple[Path, ...] = ()) -> dict:
    """Check that ``raw`` can be the workspace, without changing anything but a test file that is removed again.

    Returns ``path`` (the canonical folder runs would really be written to), ``exists``, ``base`` (for a folder
    that doesn't exist yet: the existing folder it would be created in), ``link`` (the path leads somewhere else)
    and ``runs`` (Machyon runs already there). Raises FolderProblem with the reason when it can't be used.
    ``forbidden``: folders Machyon itself uses that must not hold a workspace (its staging folder)."""
    if not isinstance(raw, str):
        raise FolderProblem("Enter a folder path.")
    text = _unquote(raw)
    if not text:
        raise FolderProblem("Enter a folder path.")
    if len(text) > PATH_MAX or "\x00" in text:
        raise FolderProblem("That isn't a usable folder path.")
    windows = os.name == "nt"
    expanded = os.path.expanduser(text)
    pure = PureWindowsPath(expanded) if windows else PurePosixPath(expanded)
    if not pure.is_absolute():
        example = "D:\\Datasets" if windows else "/home/you/datasets"
        raise FolderProblem(f"Enter the full path of a folder, such as {example}.")
    if windows:
        why = windows_name_problem(expanded)
        if why:
            raise FolderProblem(why)
        anchor = pure.anchor
        if not os.path.exists(anchor):
            where = f"Drive {pure.drive}" if len(pure.drive) == 2 else f"The network location {pure.drive}"
            raise FolderProblem(f"{where} isn't available. Connect it, or choose another folder.")
    entered = Path(os.path.abspath(expanded))
    try:
        path = entered.resolve(strict=False)
    except (OSError, RuntimeError) as e:   # a symlink loop, an unreadable link
        raise FolderProblem(f"That path can't be followed ({e}).") from None
    if path.exists() and not path.is_dir():
        raise FolderProblem("That's a file, not a folder.")
    exists = path.is_dir()
    base = path
    while not base.exists():
        if base.parent == base:
            raise FolderProblem("None of that path exists on this computer.")
        base = base.parent
    if not base.is_dir():
        raise FolderProblem(f"{base} is a file, so no folder can be made inside it.")
    for anc in (path, *path.parents):
        if RUN_ID.match(anc.name) and (anc / "run.json").is_file():
            raise FolderProblem(f"That's inside the Machyon run {anc.name}. Choose a folder outside any run.")
        if anc.name == ".staging" and (anc.parent / LOCK_NAME).exists():
            raise FolderProblem("That's Machyon's own staging folder. Choose another.")
    for f in forbidden:
        if path == f or path.is_relative_to(f):
            raise FolderProblem("That's Machyon's own staging folder. Choose another.")
    _probe_write(path if exists else base)
    runs = runs_in(path) if exists else 0
    same = os.path.normcase(str(entered)) == os.path.normcase(str(path))
    return {"path": str(path), "exists": exists, "base": None if exists else str(base), "link": not same,
            "runs": runs}


def _probe_write(folder: Path) -> None:
    """Create and remove a small file: the only reliable test of writing there (os.access can't see Windows ACLs,
    read-only media or a full disk)."""
    probe = folder / f".machyon-write-test-{secrets.token_hex(4)}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, b"machyon")
            os.fsync(fd)
        finally:
            os.close(fd)
    except PermissionError:
        raise FolderProblem(f"You don't have permission to write in {folder}.") from None
    except OSError as e:
        if e.errno == errno.EROFS:
            raise FolderProblem(f"{folder} is read-only.") from None
        if e.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            raise FolderProblem(f"There's no space left in {folder}.") from None
        raise FolderProblem(f"Machyon can't write in {folder}: {e.strerror or e}.") from None
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass


def runs_in(path: Path) -> int:
    """How many Machyon runs ``path`` holds."""
    try:
        return sum(1 for e in os.scandir(path) if RUN_ID.match(e.name) and os.path.isfile(os.path.join(e.path, "run.json")))
    except OSError:
        return 0


# ------------------------------------------------------------------ staging

class Staging:
    """Where uploaded and pasted manifests wait until a download starts: one folder per launch, outside every
    workspace, so changing the workspace never loses a manifest that is ready to go. It holds its own lock, so a
    later launch can tell a crashed launch's leftovers (removed) from another running one's (left alone)."""

    def __init__(self, base: Path | None = None) -> None:
        tag = os.getuid() if hasattr(os, "getuid") else "user"
        self.base = Path(base) if base is not None else Path(tempfile.gettempdir()) / f"machyon-ui-staging-{tag}"
        self.base.mkdir(mode=0o700, exist_ok=True)
        self._sweep()
        self.dir = Path(tempfile.mkdtemp(prefix="launch-", dir=self.base))
        self.lock = RootLock(self.dir)

    def _sweep(self) -> None:
        try:
            entries = [e for e in os.scandir(self.base) if e.name.startswith("launch-") and e.is_dir()]
        except OSError:
            return
        for e in entries:
            try:
                lock = RootLock(Path(e.path))
            except OSError:
                continue            # in use by a running machyon ui
            lock.release()
            shutil.rmtree(e.path, ignore_errors=True)

    def close(self) -> None:
        self.lock.release()
        shutil.rmtree(self.dir, ignore_errors=True)


# ------------------------------------------------------------------ the launch decision

@dataclass
class Choice:
    """The workspace a launch uses, and why. ``source``: "cli" (--output-dir, this launch only), "saved" (the
    folder last chosen in the app), "default", "fallback" (the default, because the saved folder is unavailable:
    ``fallback`` says why) or, after a change in the app, "chosen"."""

    path: str
    source: str
    saved: str | None
    fallback: str | None = None
    prefs: Prefs | None = None


def choose_at_launch(output_dir: str | None, prefs: Prefs) -> Choice:
    saved = prefs.workspace
    if output_dir is not None:
        return Choice(output_dir, "cli", saved, prefs=prefs)
    if saved:
        try:
            info = check_folder(saved)
        except FolderProblem as e:
            return Choice(DEFAULT_WORKSPACE, "fallback", saved, str(e), prefs)
        if not info["exists"]:
            return Choice(DEFAULT_WORKSPACE, "fallback", saved, "The folder doesn't exist any more.", prefs)
        return Choice(info["path"], "saved", saved, prefs=prefs)
    return Choice(DEFAULT_WORKSPACE, "default", None, prefs=prefs)


def same_place(a, b) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
