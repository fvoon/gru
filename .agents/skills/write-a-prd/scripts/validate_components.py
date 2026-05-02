"""Validate that PLTPM's 4 canonical repo-shaped Components exist in Jira.

Reads the canonical names from `.agents/jira-conventions.md` (never hardcoded) and either:
- emits a JSON action plan instructing the LLM to call `atlassian.getJiraProjectComponents`
  (default mode), OR
- verifies a fetched fixture against the canonical list (`--components-fixture`).

Exit codes:
  0 - ok (all canonical components present in fixture, or action plan emitted)
  1 - missing (one or more canonical components absent from fixture)
  2 - usage / IO / parse error

The script never calls Atlassian APIs itself; the LLM in chat is the executor. This keeps
the script pure and testable in isolation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def find_conventions(start: Path) -> Path:
    """Walk up from `start` looking for `.agents/jira-conventions.md`."""
    cur = start.resolve()
    for _ in range(8):
        candidate = cur / ".agents" / "jira-conventions.md"
        if candidate.exists():
            return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    raise FileNotFoundError(
        f"could not find .agents/jira-conventions.md walking up from {start}"
    )


def parse_canonical_components(conventions_text: str) -> list[str]:
    """Extract the canonical repo-shaped Component names from the conventions markdown.

    Locates the `### Jira Components` H3 section and returns the first contiguous block of
    backtick-quoted bullet items (e.g. ``- `payment-platform` ``).
    """
    section_re = re.compile(
        r"^### Jira Components.*?(?=^### |^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    section_match = section_re.search(conventions_text)
    if not section_match:
        raise ValueError("could not find '### Jira Components' section in conventions")

    section = section_match.group(0)
    bullet_re = re.compile(r"^- `([^`]+)`\s*$")
    components: list[str] = []
    for line in section.splitlines():
        m = bullet_re.match(line)
        if m:
            components.append(m.group(1))
        elif components:
            break

    if not components:
        raise ValueError(
            "no `name` bullet items found in '### Jira Components' section"
        )
    return components


def emit_action_plan(canonical: list[str]) -> dict:
    """Return the JSON action plan for the LLM to fetch and re-feed components."""
    return {
        "status": "needs_fetch",
        "canonical": canonical,
        "actions": [
            {
                "step": 1,
                "tool": "atlassian.getJiraProjectComponents",
                "args": {"projectKey": "PLTPM"},
                "purpose": (
                    "Fetch current Components in PLTPM to verify gru's 4 "
                    "repo-shaped Components exist."
                ),
            }
        ],
        "next_step": (
            "Save the response to a JSON file and re-invoke: "
            "validate_components.py --components-fixture <path>. "
            "The script will then verify each canonical name is present."
        ),
    }


def verify(canonical: list[str], fixture: list) -> dict:
    """Compare canonical names against a fetched components fixture.

    `fixture` is expected to be the list returned by `atlassian.getJiraProjectComponents`,
    where each element is a dict with at least a `name` key.
    """
    fetched_names = set()
    for entry in fixture:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            fetched_names.add(entry["name"])

    missing = [name for name in canonical if name not in fetched_names]
    if missing:
        return {
            "status": "missing",
            "missing": missing,
            "canonical": canonical,
            "fetched": sorted(fetched_names),
            "remediation": (
                "Create the missing Components manually under PLTPM admin: "
                "https://moneylion.atlassian.net/jira/software/c/projects/PLTPM/components. "
                "See .agents/jira-conventions.md bootstrap checklist."
            ),
        }
    return {"status": "ok", "canonical": canonical}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--conventions-path",
        type=Path,
        default=None,
        help="Path to .agents/jira-conventions.md (default: walk up from script).",
    )
    parser.add_argument(
        "--components-fixture",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file containing the response from "
            "atlassian.getJiraProjectComponents. If provided, verifies; "
            "otherwise emits an action plan."
        ),
    )
    args = parser.parse_args(argv)

    try:
        conventions_path = args.conventions_path or find_conventions(Path(__file__).parent)
        conventions_text = conventions_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    try:
        canonical = parse_canonical_components(conventions_text)
    except ValueError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    if args.components_fixture is None:
        print(json.dumps(emit_action_plan(canonical), indent=2))
        return 0

    try:
        fixture_raw = args.components_fixture.read_text(encoding="utf-8")
        fixture = json.loads(fixture_raw)
    except (FileNotFoundError, OSError) as exc:
        print(
            json.dumps({"status": "error", "reason": f"could not read fixture: {exc}"}),
            file=sys.stderr,
        )
        return 2
    except json.JSONDecodeError as exc:
        print(
            json.dumps({"status": "error", "reason": f"fixture is not valid JSON: {exc}"}),
            file=sys.stderr,
        )
        return 2

    if not isinstance(fixture, list):
        print(
            json.dumps(
                {"status": "error", "reason": "fixture must be a JSON list of components"}
            ),
            file=sys.stderr,
        )
        return 2

    result = verify(canonical, fixture)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
