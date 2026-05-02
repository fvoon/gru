"""Score a PRD draft against the gru "Significant PRD heuristic".

Reads a draft markdown file and returns a JSON decision: 'inline' (PRD fits in the parent
Jira description) or 'elevate' (PRD belongs in Confluence with a Jira summary stub).

Heuristic (per .agents/jira-conventions.md "Significant PRD heuristic"):
  - More than 10 numbered items under '## User Stories'                  -> elevate
  - More than 3 non-trivial bullets (>50 chars after '<repo>:' prefix)
    under '## Cross-Application Impact'                                  -> elevate
  - Any mermaid fenced code block with more than 30 inner lines          -> elevate
  - Draft contains the literal HTML comment '<!-- elevate-to-confluence -->' -> elevate
  - Otherwise                                                            -> inline

Exit codes:
  0 - decision emitted (inline or elevate); read stdout JSON
  2 - draft missing or malformed (required sections absent)

The script has no side effects and never calls APIs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ELEVATE_MARKER = "<!-- elevate-to-confluence -->"
USER_STORY_THRESHOLD = 10
CROSS_APP_THRESHOLD = 3
MERMAID_LINE_THRESHOLD = 30
NON_TRIVIAL_DESCRIPTION_CHARS = 50

REQUIRED_SECTIONS = ("User Stories", "Cross-Application Impact")


class MalformedDraft(Exception):
    """Raised when a draft is missing sections required by the significance heuristic."""


def split_top_level_sections(text: str) -> dict[str, str]:
    """Split markdown into top-level sections by `^## ` header.

    Returns {title: body}. Body excludes the header line itself. The leading prologue
    (before any `## ` header) is stored under the empty-string key.
    """
    sections: dict[str, str] = {}
    current_title = ""
    current_lines: list[str] = []
    header_re = re.compile(r"^##\s+(.+?)\s*$")

    for line in text.splitlines():
        m = header_re.match(line)
        if m:
            sections[current_title] = "\n".join(current_lines).rstrip()
            current_title = m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)
    sections[current_title] = "\n".join(current_lines).rstrip()
    return sections


_NUMBERED_ITEM_RE = re.compile(r"^\d+\.\s+\S", re.MULTILINE)


def count_numbered_items(body: str) -> int:
    """Count top-level numbered list items (lines starting with `<digits>. `)."""
    return len(_NUMBERED_ITEM_RE.findall(body))


# Allow repo names like `payment-platform`, `walletapi/transfer`, `spring-boot.starters`
# anything before the first colon counts as the repo prefix; the description is what follows.
_CROSS_APP_BULLET_RE = re.compile(r"^-\s+(\S[^:]*):\s*(.+)$", re.MULTILINE)


def count_non_trivial_cross_app_bullets(
    body: str, threshold_chars: int = NON_TRIVIAL_DESCRIPTION_CHARS
) -> int:
    """Count bullets shaped `- <repo>: <description>` whose description exceeds threshold."""
    count = 0
    for m in _CROSS_APP_BULLET_RE.finditer(body):
        description = m.group(2).strip()
        if len(description) > threshold_chars:
            count += 1
    return count


_MERMAID_BLOCK_RE = re.compile(r"^```mermaid\s*\n(.*?)^```", re.DOTALL | re.MULTILINE)


def max_mermaid_block_lines(text: str) -> int:
    """Return the largest mermaid fenced block's inner line count, or 0 if none.

    Inner = lines between the opening and closing fence (exclusive). Trailing newline
    after the last content line is not counted.
    """
    biggest = 0
    for m in _MERMAID_BLOCK_RE.finditer(text):
        inner = m.group(1).rstrip("\n")
        n_lines = 0 if not inner else inner.count("\n") + 1
        biggest = max(biggest, n_lines)
    return biggest


def score(draft_text: str) -> dict:
    """Score a draft against the significance heuristic.

    Raises MalformedDraft if a required section is absent.
    Returns a dict with 'decision', 'reasons', and 'metrics'.
    """
    sections = split_top_level_sections(draft_text)
    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise MalformedDraft(
            f"required section(s) missing: {', '.join('## ' + s for s in missing)}"
        )

    n_stories = count_numbered_items(sections["User Stories"])
    n_xapp = count_non_trivial_cross_app_bullets(sections["Cross-Application Impact"])
    max_mermaid = max_mermaid_block_lines(draft_text)
    has_marker = ELEVATE_MARKER in draft_text

    reasons: list[str] = []
    if n_stories > USER_STORY_THRESHOLD:
        reasons.append(f">{USER_STORY_THRESHOLD} user stories ({n_stories})")
    if n_xapp > CROSS_APP_THRESHOLD:
        reasons.append(
            f">{CROSS_APP_THRESHOLD} non-trivial Cross-Application Impact entries ({n_xapp})"
        )
    if max_mermaid > MERMAID_LINE_THRESHOLD:
        reasons.append(
            f"mermaid block with {max_mermaid} lines (>{MERMAID_LINE_THRESHOLD})"
        )
    if has_marker:
        reasons.append("explicit elevate-to-confluence marker present")

    return {
        "decision": "elevate" if reasons else "inline",
        "reasons": reasons,
        "metrics": {
            "user_stories": n_stories,
            "cross_app_impact_entries": n_xapp,
            "max_mermaid_lines": max_mermaid,
            "has_elevate_marker": has_marker,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--draft", type=Path, required=True, help="Path to draft markdown file."
    )
    args = parser.parse_args(argv)

    try:
        text = args.draft.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    try:
        result = score(text)
    except MalformedDraft as exc:
        print(
            json.dumps({"status": "error", "reason": f"malformed draft: {exc}"}),
            file=sys.stderr,
        )
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
