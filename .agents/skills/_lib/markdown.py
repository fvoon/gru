"""Shared markdown parsing helpers for gru skills.

Exists because multiple skills (`write-a-prd`, `prd-to-jira-issues`, and likely future
ones) need to split markdown into top-level sections, extract titles, and read keyvalue
metadata blocks. Each helper is intentionally minimal — pure regex over splitlines, no
external dependencies — so scripts that import it stay testable in isolation.

Importers add this directory to `sys.path`; see e.g.
`.agents/skills/write-a-prd/scripts/significance_check.py`.
"""

from __future__ import annotations

import re

__all__ = [
    "split_top_level_sections",
    "extract_h1_title",
    "parse_keyvalue_block",
]


_H2_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$")
_H1_HEADER_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def split_top_level_sections(text: str) -> dict[str, str]:
    """Split markdown into top-level sections by `^## ` header.

    Returns `{title: body}`. The body excludes the header line itself and is right-
    stripped. The leading prologue (any content before the first `## ` header) is
    stored under the empty-string key.

    Subsequent occurrences of the same H2 title overwrite earlier ones; this is
    consistent with the "last write wins" intuition for hand-edited drafts.
    """
    sections: dict[str, str] = {}
    current_title = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        m = _H2_HEADER_RE.match(line)
        if m:
            sections[current_title] = "\n".join(current_lines).rstrip()
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_title] = "\n".join(current_lines).rstrip()
    return sections


def extract_h1_title(text: str) -> str:
    """Return the first `# ` header text, or `''` if absent.

    Matches at any line position (not only the first line). Trailing whitespace is
    stripped.
    """
    m = _H1_HEADER_RE.search(text)
    return m.group(1).strip() if m else ""


_KEYVALUE_LINE_RE = re.compile(r"^-\s+([\w_-]+)\s*:\s*(.*)$")
_INDENTED_BLOCK_LINE_RE = re.compile(r"^[ \t]+(.*)$")


def parse_keyvalue_block(text: str) -> dict[str, str]:
    """Parse a YAML-flavoured keyvalue block embedded in markdown.

    Recognises two shapes:

    1. Single-line scalar:    `- key: value`
    2. Multi-line block:      `- key: |` followed by indented content lines

    For block scalars, indentation is detected from the first content line (any
    consistent leading whitespace) and stripped from every subsequent line. The
    block ends at the first non-indented, non-blank line OR at the next keyvalue
    line (`- key:` at column 0).

    Unknown shapes are silently ignored — callers validate required keys
    themselves. Returns `{key: value}`; multi-line block scalars carry their
    trailing newline so callers can pass them straight to issue-description fields.
    """
    result: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        m = _KEYVALUE_LINE_RE.match(line)
        if not m:
            i += 1
            continue

        key, scalar = m.group(1), m.group(2)
        if scalar.strip() == "|":
            block_lines, consumed = _consume_indented_block(lines, i + 1)
            result[key] = "\n".join(block_lines)
            i += 1 + consumed
        else:
            result[key] = scalar.strip()
            i += 1

    return result


def _consume_indented_block(lines: list[str], start: int) -> tuple[list[str], int]:
    """Helper: pull indented lines for a block scalar starting at `start`.

    Returns (`stripped_lines`, `consumed_count`). The block ends at the first non-
    indented, non-blank line, or at a new keyvalue line at column 0.
    """
    indent: str | None = None
    out: list[str] = []
    consumed = 0

    for j in range(start, len(lines)):
        line = lines[j]
        if not line.strip():
            out.append("")
            consumed += 1
            continue
        if _KEYVALUE_LINE_RE.match(line):
            break
        m = _INDENTED_BLOCK_LINE_RE.match(line)
        if not m:
            break
        if indent is None:
            indent = line[: len(line) - len(line.lstrip())]
        if line.startswith(indent):
            out.append(line[len(indent):])
        else:
            out.append(line.lstrip())
        consumed += 1

    while out and out[-1] == "":
        out.pop()

    return out, consumed
