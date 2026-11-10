"""Runs on disk: the only record of what the UI did. No database; the folders are the history.

    <root>/run_2026-11-10_143005_a7f3/
        images/                    what Machyon downloaded, plus its own failed_downloads.jsonl
        source/manifest.<ext>      the manifest exactly as received
        run.json                   settings, status and the latest progress checkpoint
        report.json                the Report of the last download or resume, once one finishes

Every metadata write is atomic (a temporary file, then a rename), so a crash leaves the previous version intact.
Folders that don't look like runs, or whose run.json is unreadable or from a newer schema, are left alone.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("machyon.ui")

SCHEMA = 1
RUN_ID = re.compile(r"^run_\d{4}-\d{2}-\d{2}_\d{6}_[0-9a-f]{4}$")
STATUSES = frozenset({"running", "completed", "cancelled", "interrupted", "error"})
SOURCE_EXTS = (".csv", ".tsv", ".txt", ".json", ".jsonl", ".ndjson", ".parquet")
_SOURCE_FILE = re.compile(r"^source/manifest\.(csv|tsv|txt|json|jsonl|ndjson|parquet)$")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json_atomic(path: Path, data) -> None:
    """Replace ``path`` with ``data`` so that a reader sees the old file or the new one, never half of either."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: someone (antivirus, an indexer) has the old file open for a moment
                if attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def valid_run(data, run_id: str) -> str | None:
    """Why ``data`` is not a run.json this version can use, or ``None`` when it is."""
    if not isinstance(data, dict):
        return "not a JSON object"
    schema = data.get("schema")
    if schema != SCHEMA:
        return f"schema {schema!r} (this Machyon reads schema {SCHEMA})"
    if data.get("id") != run_id:
        return "its id doesn't match its folder"
    if data.get("status") not in STATUSES:
        return f"unknown status {data.get('status')!r}"
    source = data.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("file"), str) or not _SOURCE_FILE.match(source["file"]):
        return "no valid source"
    if not isinstance(data.get("settings"), dict):
        return "no settings"
    if data.get("names") is not None and not isinstance(data["names"], str):
        return "invalid filename template"
    return None


class RunStore:
    """Every run folder under one output root. Paths are only ever built from validated run ids."""

    def __init__(self, root) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.staging = self.root / ".staging"

    # ------------------------------------------------------------------ paths

    def path(self, run_id) -> Path:
        """The folder of ``run_id``. Raises KeyError for anything that isn't a run folder directly in the root."""
        if not isinstance(run_id, str) or not RUN_ID.match(run_id):
            raise KeyError(run_id)
        p = self.root / run_id
        if p.is_symlink() or not p.is_dir() or p.resolve().parent != self.root:
            raise KeyError(run_id)
        return p

    def images(self, run_id) -> Path:
        return self.path(run_id) / "images"

    def source(self, run: dict) -> Path:
        return self.path(run["id"]) / run["source"]["file"]

    # ------------------------------------------------------------------ reading

    def load(self, run_id) -> dict:
        """run.json of ``run_id``. Raises KeyError if it isn't a readable run."""
        p = self.path(run_id)
        try:
            data = read_json(p / "run.json")
        except (OSError, ValueError) as e:
            raise KeyError(run_id) from e
        if valid_run(data, run_id):
            raise KeyError(run_id)
        return data

    def report(self, run_id) -> dict | None:
        try:
            data = read_json(self.path(run_id) / "report.json")
        except (OSError, ValueError):
            return None
        return data.get("report") if isinstance(data, dict) and data.get("schema") == SCHEMA else None

    def list(self) -> list[dict]:
        """Every readable run, newest first. Unreadable ones are skipped, with a warning in the console."""
        runs = []
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return runs
        for e in entries:
            if not RUN_ID.match(e.name) or e.is_symlink() or not e.is_dir():
                continue
            try:
                data = read_json(Path(e.path) / "run.json")
            except (OSError, ValueError) as err:
                logger.warning(f"Skipping {e.name}: run.json can't be read ({err})")
                continue
            why = valid_run(data, e.name)
            if why:
                logger.warning(f"Skipping {e.name}: {why}")
                continue
            runs.append(data)
        runs.sort(key=lambda r: (r["id"][:21], r.get("created_ts", 0)), reverse=True)   # newest first
        return runs

    # ------------------------------------------------------------------ writing

    def save(self, run: dict) -> None:
        run["updated"] = now_iso()
        write_json_atomic(self.path(run["id"]) / "run.json", run)

    def save_report(self, run_id: str, report: dict) -> None:
        write_json_atomic(self.path(run_id) / "report.json", {"schema": SCHEMA, "report": report})

    def create(self, staged: Path, source: dict, settings: dict, names: str | None) -> dict:
        """Make a run folder, move the staged manifest into it, and write its first run.json."""
        for _ in range(20):
            run_id = f"run_{datetime.now():%Y-%m-%d_%H%M%S}_{secrets.token_hex(2)}"
            d = self.root / run_id
            try:
                d.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("could not create a unique run folder")
        (d / "images").mkdir()
        (d / "source").mkdir()
        rel = f"source/manifest{staged.suffix.lower()}"
        os.replace(staged, d / rel)   # same filesystem: staging lives in the root
        run = {"schema": SCHEMA, "id": run_id, "created": now_iso(), "created_ts": time.time(), "updated": now_iso(),
               "status": "running",
               "source": {**source, "file": rel}, "names": names, "settings": settings,
               "progress": None, "error": None, "history": []}
        self.save(run)
        return run

    def recover(self) -> list[str]:
        """Runs left as ``running`` by a server that no longer exists become ``interrupted``.

        Only one server uses a root at a time (see ``serve``), so a run still marked running at startup belongs to a
        process that has gone. Nothing is judged successful or failed here: the files and the ledger stay as they
        are, and resuming lets Machyon itself decide what is complete.
        """
        fixed = []
        for run in self.list():
            if run["status"] == "running":
                run["status"] = "interrupted"
                run["error"] = "Machyon UI stopped while this run was downloading."
                self.save(run)
                fixed.append(run["id"])
        return fixed

    def clear_staging(self) -> None:
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging.mkdir(exist_ok=True)
