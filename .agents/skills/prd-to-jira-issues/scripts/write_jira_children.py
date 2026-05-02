"""Convert a redlined slice plan into a deterministic Jira write action plan.

Reads `redlines/<parent-key>.md` (after PM/eng has filled in acceptance criteria
and any other edits) and emits a JSON action plan in 4 deterministic phases:

  1. createJiraIssue per slice, in alphabetic letter order.
  2. createJiraSubtask × 5 per Task / Technical Story slice
     (`Development`, `Code Review 1`, `Code Review 2`, `Test case creation`,
     `Test case execution`). Research / Design slices skip Sub-tasks.
  3. createIssueLink type `Implement`, parent → each child.
  4. createIssueLink type `Blocks` for each `depends_on:` reference, inward =
     blocker, outward = blocked (per .agents/jira-conventions.md "createIssueLink
     directionality").

Validation gates (any failure → exit 1):
- Each slice has `type`, `title`, `component`, `depends_on`, `description`.
- `type` ∈ {Task, Technical Story, Research , Design } (trailing space on the
  latter two — load-bearing).
- `component` is one of the 4 canonical repos.
- `depends_on` letters all reference defined slices in the same plan.
- No `description` contains the literal string `TODO` (the placeholder gate
  for acceptance criteria — PM/eng must fill them in before this script will
  emit actions).

Idempotency: per the `prd-to-jira-issues` plan (Issue 5B locked decision), this
script does NOT journal state. The agent re-runs on partial failure and the LLM
filters already-succeeded actions from chat context. See REFERENCE.md.

The script never calls Atlassian itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Shared markdown helpers.
SCRIPT_DIR = Path(__file__).resolve().parent
_LIB_PATH = SCRIPT_DIR.parent.parent / "_lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

from markdown import parse_keyvalue_block, split_top_level_sections  # noqa: E402

CANONICAL_COMPONENTS = (
    "payment-platform",
    "walletapi",
    "infrastructure",
    "spring-boot-starters",
)

# Internal canonical type names (no trailing space). The redline file's
# parse_keyvalue_block strips trailing whitespace, so we accept both `Research`
# and `Research ` from PM/eng input. The trailing space is re-applied at action
# emission time because Atlassian's issuetype names require it (see
# .agents/jira-conventions.md, line 45-46).
ALLOWED_TYPES = frozenset({"Task", "Technical Story", "Research", "Design"})
SUBTASK_TYPES = frozenset({"Task", "Technical Story"})
_NEEDS_TRAILING_SPACE = frozenset({"Research", "Design"})

CEREMONY_SUBTASKS = (
    "Development",
    "Code Review 1",
    "Code Review 2",
    "Test case creation",
    "Test case execution",
)

REQUIRED_KEYS = ("type", "title", "component", "depends_on", "description")

_SLICE_HEADER_RE = re.compile(r"^Slice\s+([A-Z])\b\s*[—\-]?\s*(.*?)\s*$")


class InvalidSlicePlan(Exception):
    """Raised when the slice plan does not satisfy a validation gate."""


# ----- parsing -----


def parse_slice_plan(text: str) -> list[dict]:
    """Parse the redline markdown into ordered slice dicts.

    Each dict carries the keyvalue header fields plus `letter` and `header_oneliner`
    derived from the `## Slice X — ...` header. Descriptions retain their original
    multi-line content.
    """
    sections = split_top_level_sections(text)
    slices: list[dict] = []

    for header_title, body in sections.items():
        m = _SLICE_HEADER_RE.match(header_title)
        if not m:
            continue
        letter = m.group(1)
        oneliner = m.group(2).strip()

        kv = parse_keyvalue_block(body)
        kv["letter"] = letter
        kv["header_oneliner"] = oneliner
        slices.append(kv)

    slices.sort(key=lambda s: s["letter"])
    return slices


def parse_depends_on(value: str) -> list[str]:
    """Parse `(none)`, empty, or a comma-separated list of letters."""
    v = (value or "").strip()
    if not v or v.lower() == "(none)":
        return []
    parts = [p.strip() for p in v.split(",") if p.strip()]
    return [p for p in parts if p.lower() != "(none)"]


# ----- validation -----


def validate_slices(slices: list[dict], parent_key: str) -> None:
    if not slices:
        raise InvalidSlicePlan(
            "no slices found in plan; expected at least one `## Slice X — ...` section"
        )

    letters = {s["letter"] for s in slices}
    failures: list[str] = []

    for s in slices:
        prefix = f"slice {s['letter']}"

        for key in REQUIRED_KEYS:
            if key not in s or not str(s[key]).strip():
                failures.append(f"{prefix}: missing required key `{key}`")

        if "type" in s and s["type"] not in ALLOWED_TYPES:
            failures.append(
                f"{prefix}: type {s['type']!r} not in {sorted(ALLOWED_TYPES)} "
                "(allowed: Task, Technical Story, Research, Design)"
            )

        if "component" in s and s["component"] not in CANONICAL_COMPONENTS:
            failures.append(
                f"{prefix}: component {s['component']!r} not in canonical "
                f"{list(CANONICAL_COMPONENTS)}"
            )

        if "depends_on" in s:
            for dep in parse_depends_on(s["depends_on"]):
                if dep not in letters:
                    failures.append(
                        f"{prefix}: depends_on references unknown slice {dep!r}; "
                        f"known letters: {sorted(letters)}"
                    )
                if dep == s["letter"]:
                    failures.append(f"{prefix}: depends_on cannot reference itself")

        if "description" in s and "TODO" in s["description"]:
            failures.append(
                f"{prefix}: description still contains literal `TODO` — fill in the "
                "Acceptance criteria block before running write_jira_children.py"
            )

    if not parent_key:
        failures.append("parent_key is empty")

    if failures:
        raise InvalidSlicePlan("; ".join(failures))


# ----- action-plan emission -----


def _project_key_from_parent(parent_key: str) -> str:
    """`PLTPM-21500` → `PLTPM`. Defensive against missing dashes."""
    return parent_key.split("-", 1)[0] if "-" in parent_key else parent_key


def _slice_key_token(letter: str) -> str:
    """Stable placeholder the agent substitutes after createJiraIssue returns a key."""
    return f"{{{{slice_{letter}_key}}}}"


def _atlassian_issue_type(internal_type: str) -> str:
    """Re-apply the trailing space Atlassian requires for `Research ` / `Design `."""
    if internal_type in _NEEDS_TRAILING_SPACE:
        return internal_type + " "
    return internal_type


def emit_actions(slices: list[dict], parent_key: str) -> list[dict]:
    """Build the deterministic 4-phase action sequence."""
    project_key = _project_key_from_parent(parent_key)
    actions: list[dict] = []
    step = 0

    def add(tool: str, args: dict, *, stores_as: str | None = None, purpose: str = "") -> None:
        nonlocal step
        step += 1
        action = {"step": step, "tool": tool, "args": args}
        if stores_as:
            action["stores_as"] = stores_as
        if purpose:
            action["purpose"] = purpose
        actions.append(action)

    # Phase 1: createJiraIssue per slice
    for s in slices:
        letter = s["letter"]
        add(
            "atlassian.createJiraIssue",
            {
                "projectKey": project_key,
                "issueType": _atlassian_issue_type(s["type"]),
                "summary": s["title"],
                "description": s["description"],
                "components": [{"name": s["component"]}],
            },
            stores_as=f"slice_{letter}_key",
            purpose=f"Create slice {letter} as a {s['type']} child of {parent_key}.",
        )

    # Phase 2: 5 ceremony Sub-tasks per Task/Technical Story slice
    for s in slices:
        if s["type"] not in SUBTASK_TYPES:
            continue
        letter = s["letter"]
        for subtask_name in CEREMONY_SUBTASKS:
            add(
                "atlassian.createJiraSubtask",
                {
                    "parentIssueKey": _slice_key_token(letter),
                    "summary": subtask_name,
                    "description": "",
                },
                purpose=(
                    f"Ceremony Sub-task for slice {letter} "
                    f"(per .agents/jira-conventions.md)."
                ),
            )

    # Phase 3: Implement link parent → each child
    for s in slices:
        letter = s["letter"]
        add(
            "atlassian.createIssueLink",
            {
                "type": "Implement",
                "inwardIssue": parent_key,
                "outwardIssue": _slice_key_token(letter),
            },
            purpose=f"Mark slice {letter} as implementing {parent_key}.",
        )

    # Phase 4: is-blocked-by chains. type=Blocks, inward=blocker, outward=blocked.
    # `depends_on: A` on slice X means X is blocked by A → A is the blocker.
    for s in slices:
        blocked_letter = s["letter"]
        for blocker_letter in parse_depends_on(s.get("depends_on", "")):
            add(
                "atlassian.createIssueLink",
                {
                    "type": "Blocks",
                    "inwardIssue": _slice_key_token(blocker_letter),
                    "outwardIssue": _slice_key_token(blocked_letter),
                },
                purpose=(
                    f"Slice {blocked_letter} is blocked by slice {blocker_letter} "
                    "(per redline depends_on: ...)."
                ),
            )

    return actions


def build_plan(slices: list[dict], parent_key: str) -> dict:
    return {
        "status": "ok",
        "parent_key": parent_key,
        "slice_count": len(slices),
        "letters": [s["letter"] for s in slices],
        "actions": emit_actions(slices, parent_key),
    }


# ----- CLI -----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--slice-plan",
        type=Path,
        required=True,
        help="Path to the redlined slice plan markdown file.",
    )
    parser.add_argument(
        "--parent-key",
        required=True,
        help="Parent issue key, e.g. PLTPM-21500.",
    )
    args = parser.parse_args(argv)

    try:
        text = args.slice_plan.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    try:
        slices = parse_slice_plan(text)
        validate_slices(slices, args.parent_key)
    except InvalidSlicePlan as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}))
        return 1

    plan = build_plan(slices, args.parent_key)
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
