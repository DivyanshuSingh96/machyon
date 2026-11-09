"""Docs-only griffe extension: renders Machyon's docstrings well without changing the library.

1. Sphinx roles (:class:`Report`, :meth:`retry_failed`) become cross-references in the API reference.
2. Trailing ``#:`` comments (how ``Report`` documents its fields) become attribute docstrings.
3. reST literal blocks (a ``::`` paragraph followed by an indented block) become fenced Python code.
"""
from __future__ import annotations

import re

import griffe

PUBLIC = {
    "download", "adownload", "Downloader", "DownloadConfig", "Report", "NameTemplate", "read_rows", "run",
    "setup_logging", "MachyonError", "FatalDiskError", "AdaptiveLimiter", "ImageDownloader", "ImageRenamer",
    "generate_hashed_name", "download_images", "Config",
}
ROLE = re.compile(r":(?:class|meth|func|attr|exc|mod|obj):`~?([\w.]+)`")
LITERAL = re.compile(r"^(?P<lead>.*?)::[ \t]*\n[ \t]*\n(?P<body>(?:(?:[ \t]+.*)?\n)+?)(?=\S|\Z)", re.M)


def _fence(match: re.Match) -> str:
    lead = match.group("lead").rstrip()
    body = match.group("body").rstrip("\n").splitlines()
    indent = min(len(line) - len(line.lstrip()) for line in body if line.strip())
    code = "\n".join(line[indent:] for line in body)
    head = f"{lead}:\n\n" if lead else ""   # "Example::" keeps one colon; a bare "::" disappears
    return f"{head}```python\n{code}\n```\n\n"


def _render(text: str) -> str:
    return ROLE.sub(_ref, LITERAL.sub(_fence, text + "\n")).rstrip("\n")


def _ref(match: re.Match) -> str:
    name = match.group(1).removeprefix("machyon.")
    head = name.split(".")[0]
    if head in PUBLIC:
        return f"[`{name}`][machyon.{name}]"
    return f"`{name}`"


class MachyonDocs(griffe.Extension):
    def on_package(self, *, pkg: griffe.Module, **kwargs) -> None:
        self._walk(pkg)

    def _walk(self, obj) -> None:
        for member in list(obj.members.values()):
            if member.is_alias:
                continue
            if member.is_attribute and member.docstring is None:
                self._from_comment(member)
            if member.docstring is not None:
                member.docstring.value = _render(member.docstring.value)
            if member.is_class or member.is_module:
                self._walk(member)
        if obj.docstring is not None:
            obj.docstring.value = _render(obj.docstring.value)

    @staticmethod
    def _from_comment(attr) -> None:
        try:
            line = attr.module.lines[attr.lineno - 1]
        except (AttributeError, IndexError, TypeError):
            return
        if "#:" in line:
            text = line.split("#:", 1)[1].strip()
            if text:
                attr.docstring = griffe.Docstring(text, parent=attr, lineno=attr.lineno)
