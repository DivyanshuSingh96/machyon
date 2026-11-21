"""The Discord release announcement: .github/discord-release.jq and the workflow that runs it."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JQ = ROOT / ".github" / "discord-release.jq"
WORKFLOW = ROOT / ".github" / "workflows" / "discord-release.yml"

NOTES = (
    "The same code as 1.2.3, released again so the project page on PyPI shows the current README.\r\n\r\n"
    "**Changed**\r\n\r\n- The benchmarks were run again.\r\n- The README links to the Machyon Community on Discord."
)


def event(**release):
    r = {"tag_name": "v1.2.3.post1", "name": "Machyon 1.2.3.post1", "body": NOTES, "prerelease": False,
         "html_url": "https://github.com/DivyanshuSingh96/machyon/releases/tag/v1.2.3.post1",
         "published_at": "2026-11-21T04:57:31Z"}
    r.update(release)
    return {"action": "published", "release": r, "repository": {"full_name": "DivyanshuSingh96/machyon"}}


def build(ev, tmp_path):
    if not shutil.which("jq"):
        pytest.skip("jq isn't installed (GitHub's runners have it)")
    path = tmp_path / "event.json"
    path.write_text(json.dumps(ev), encoding="utf-8")
    out = subprocess.run(["jq", "-f", str(JQ), str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def within_discord_limits(msg):
    (embed,) = msg["embeds"]
    assert len(embed["title"]) <= 256
    assert len(embed["description"]) <= 4096
    assert all(len(f["name"]) <= 256 and len(f["value"]) <= 1024 for f in embed["fields"])
    total = len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"])
    total += sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
    assert total <= 6000


def test_release_becomes_a_titled_embed_with_notes(tmp_path):
    msg = build(event(), tmp_path)
    within_discord_limits(msg)
    (embed,) = msg["embeds"]
    assert embed["title"] == "Machyon 1.2.3.post1"
    assert embed["url"].endswith("/releases/tag/v1.2.3.post1")
    assert embed["description"].startswith("The same code as 1.2.3")
    assert "\r" not in embed["description"] and "- The README links to" in embed["description"]
    assert "pip install -U machyon==1.2.3.post1" in embed["fields"][0]["value"]
    assert "https://pypi.org/project/machyon/1.2.3.post1/" in embed["fields"][1]["value"]
    assert embed["timestamp"] == "2026-11-21T04:57:31Z"


def test_mentions_never_ping(tmp_path):
    msg = build(event(body="Thanks @everyone and @here!"), tmp_path)
    assert msg["allowed_mentions"] == {"parse": []}


def test_long_notes_are_cut_at_a_line_break_with_a_link(tmp_path):
    body = "\n".join(f"- change number {i}, described at some length so the notes grow quickly" for i in range(300))
    msg = build(event(body=body), tmp_path)
    within_discord_limits(msg)
    desc = msg["embeds"][0]["description"]
    kept = desc.split("\n\n*The notes go on")[0]
    assert kept.endswith("quickly")             # whole lines only
    assert "[read them in full](https://github.com/DivyanshuSingh96/machyon/releases/tag/v1.2.3.post1)" in desc


def test_missing_title_or_notes(tmp_path):
    msg = build(event(name="", body=None), tmp_path)
    assert msg["embeds"][0]["title"] == "Machyon 1.2.3.post1"
    assert msg["embeds"][0]["description"] == "No notes for this release."
    msg = build(event(name="x" * 400), tmp_path)
    within_discord_limits(msg)


def test_workflow_is_separate_safe_and_skips_pre_releases():
    text = WORKFLOW.read_text(encoding="utf-8")
    run = text.split("run: |", 1)[1]
    assert "types: [published]" in text
    assert "!github.event.release.prerelease" in text
    assert "secrets.DISCORD_RELEASE_WEBHOOK" in text
    assert "${{" not in run                     # release text never reaches the shell through an expression
    assert "needs:" not in text and "publish" not in text.split("jobs:", 1)[1]     # independent of the PyPI job
