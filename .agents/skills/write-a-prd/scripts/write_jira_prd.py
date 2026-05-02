"""Plan a Jira (and optionally Confluence) write for a PRD draft.

Reads a draft markdown file, applies gru's parent-type and Component heuristics, and
emits a JSON action plan that an LLM in chat can execute through the Atlassian MCP.

The script is pure: no API calls, no side effects. Idempotent — same input produces
identical output.

Inputs:
  --draft PATH               Path to the draft markdown.
  --decision-json PATH       Optional. JSON output from significance_check.py for the same
                             draft. If absent, --mode is required and trusted.
  --mode {inline,elevate}    Override mode (when --decision-json is not provided).
  --confluence-space KEY     Confluence space key for the elevated PRD page (default PS).
  --project-key KEY          Jira project key (default PLTPM).

Output:
  stdout: JSON action plan (see REFERENCE.md for schema).
  exit 0 - plan emitted (whether or not requires_user_choice is set)
  exit 2 - draft missing / malformed / inputs inconsistent

Tool names in actions are SEMANTIC INTENTS (e.g. `atlassian.createJiraIssue`). The
caller LLM resolves them to concrete MCP tool names from whichever Atlassian MCP is
mounted in the runtime.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Reuse the parser from significance_check; one source of truth for section splitting.
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from significance_check import (  # noqa: E402
    ELEVATE_MARKER,
    split_top_level_sections,
)

# ----- canonical config (must stay in lockstep with .agents/jira-conventions.md) -----

CANONICAL_COMPONENTS = (
    "payment-platform",
    "walletapi",
    "infrastructure",
    "spring-boot-starters",
)

REQUIRED_SECTIONS = (
    "Problem Statement",
    "Solution",
    "User Stories",
    "Cross-Application Impact",
    "Implementation Decisions",
    "Out of Scope",
    "Further Notes",
    "References",
)

# Heuristic for "system actor": kept conservative because "when in doubt → Story" per
# .agents/jira-conventions.md. Only two strong signals trigger the system classification:
#   1. The actor name contains a hyphen (kebab-case / repo-shaped identifier, e.g.
#      `payment-platform`, `transfer-saga`).
#   2. The actor name contains an internal capital letter (CamelCase identifier, e.g.
#      `TransferSaga`, `SQS consumer`).
# Plain lowercase prose actors ("a customer", "the merchant ops user") fall through to
# user-facing → Story. Edge cases like "an engineer" are ambiguous and intentionally
# default to Story; PMs override at write time.

SUMMARY_SOFT_LIMIT = 100
SUMMARY_HARD_LIMIT = 255

CONFLUENCE_PLACEHOLDER = "<CONFLUENCE_PAGE_URL>"


class InvalidDraft(Exception):
    """Raised when a draft cannot be turned into a valid action plan."""


# ----- parsing helpers -----


def extract_h1_title(text: str) -> str:
    """Return the first `# ` header text, or '' if absent."""
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


_AS_ACTOR_RE = re.compile(
    r"^\d+\.\s+As\s+(?:(?:an?|the)\s+)?([\w./-]+(?:\s+[\w./-]+)?)",
    re.MULTILINE | re.IGNORECASE,
)


def detect_user_story_actors(user_stories_body: str) -> list[str]:
    """Pull the actor phrase out of each `1. As <a|an|the>? <actor>, ...` story.

    The article is optional so that bare-identifier actors like
    "As payment-platform, I want..." are captured.
    """
    return [m.group(1).strip() for m in _AS_ACTOR_RE.finditer(user_stories_body)]


def is_system_actor(actor: str) -> bool:
    """True when actor name is a code-shaped identifier (kebab-case or CamelCase)."""
    if "-" in actor:
        return True
    return any(c.isupper() for c in actor[1:])


def detect_parent_type(user_stories_body: str) -> str:
    """Apply the parent-type heuristic from .agents/jira-conventions.md.

    All actors system-y -> 'Technical Story'.
    Otherwise (including no `As a` stories at all -> "when in doubt → Story") -> 'Story'.
    """
    actors = detect_user_story_actors(user_stories_body)
    if actors and all(is_system_actor(a) for a in actors):
        return "Technical Story"
    return "Story"


_CROSS_APP_BULLET_RE = re.compile(r"^-\s+(\S[^:]*?):\s*\S", re.MULTILINE)


def detect_components(cross_app_body: str) -> list[str]:
    """Extract canonical-component names referenced as bullet prefixes.

    Returns the canonical names in `CANONICAL_COMPONENTS` order (stable / deterministic).
    Non-canonical bullets are ignored — child tickets handle them via prd-to-jira-issues,
    but the parent's Component must be one of the 4 canonical repos.
    """
    refs: set[str] = set()
    for m in _CROSS_APP_BULLET_RE.finditer(cross_app_body):
        candidate = m.group(1).strip()
        if candidate in CANONICAL_COMPONENTS:
            refs.add(candidate)
    return [c for c in CANONICAL_COMPONENTS if c in refs]


def truncate_summary(summary: str) -> str:
    """Hard-cap summary at SUMMARY_HARD_LIMIT chars, ellipsis-truncating if needed.

    Soft limit (100) is advisory only — not enforced here.
    """
    if len(summary) <= SUMMARY_HARD_LIMIT:
        return summary
    return summary[: SUMMARY_HARD_LIMIT - 3] + "..."


def strip_h1_and_marker(text: str) -> str:
    """Remove the H1 header (used as summary) and the elevate marker from a draft."""
    no_h1 = re.sub(r"^#\s+.+?\n+", "", text, count=1, flags=re.MULTILINE)
    return no_h1.replace(ELEVATE_MARKER, "").strip() + "\n"


# ----- description composers -----


def compose_inline_description(draft_text: str, sections: dict[str, str]) -> str:
    """Inline mode: clean draft body (drop H1 and elevate marker)."""
    return strip_h1_and_marker(draft_text)


def compose_elevate_description(
    title: str,
    sections: dict[str, str],
    components: list[str],
    placeholder: str = CONFLUENCE_PLACEHOLDER,
) -> str:
    """Elevate mode: terse summary that points at the Confluence page.

    Includes Problem Statement (verbatim), Out of Scope (verbatim), and a Cross-App
    repo list. Children continue to reference the Jira parent (not the Confluence page).
    """
    repo_list = "\n".join(f"- {c}" for c in components) if components else "- (TBD)"
    body = (
        "**This is a significant PRD; the full text lives in Confluence.**\n"
        f"\n"
        f"**Confluence**: {placeholder}\n"
        f"\n"
        f"## Problem Statement\n{sections['Problem Statement'].strip()}\n"
        f"\n"
        f"## Out of Scope\n{sections['Out of Scope'].strip()}\n"
        f"\n"
        f"## Cross-Application Impact\n{repo_list}\n"
        f"\n"
        f"---\n"
        f"See Confluence for full user stories, implementation decisions, "
        f"references, and any embedded diagrams.\n"
    )
    return body


# ----- core planner -----


def plan(
    draft_text: str,
    mode: str,
    project_key: str = "PLTPM",
    confluence_space: str = "PS",
) -> dict:
    """Build an action plan dict for the given draft + mode.

    Raises InvalidDraft on missing sections / missing H1 title / no canonical repo
    references in Cross-App Impact.
    """
    if mode not in {"inline", "elevate"}:
        raise InvalidDraft(f"unknown mode: {mode!r} (expected 'inline' or 'elevate')")

    sections = split_top_level_sections(draft_text)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise InvalidDraft(
            f"required section(s) missing: {', '.join('## ' + s for s in missing)}"
        )

    title = extract_h1_title(draft_text)
    if not title:
        raise InvalidDraft("draft must start with an `# <Title>` H1 header")

    issue_type = detect_parent_type(sections["User Stories"])
    components = detect_components(sections["Cross-Application Impact"])

    if not components:
        raise InvalidDraft(
            "Cross-Application Impact must list at least one canonical repo: "
            + ", ".join(CANONICAL_COMPONENTS)
        )

    requires_user_choice = None
    primary_component: str | None
    if len(components) == 1:
        primary_component = components[0]
    else:
        primary_component = None
        requires_user_choice = {
            "field": "primary_component",
            "prompt": (
                "Cross-Application Impact references multiple canonical repos. "
                "Which repo owns the parent ticket? (Children for the other repos "
                "will be created by prd-to-jira-issues.)"
            ),
            "options": components,
        }

    summary = truncate_summary(title)

    if requires_user_choice is not None:
        actions: list[dict] = []
    elif mode == "inline":
        description = compose_inline_description(draft_text, sections)
        actions = [
            {
                "step": 1,
                "tool": "atlassian.createJiraIssue",
                "args": {
                    "projectKey": project_key,
                    "issueType": issue_type,
                    "components": [primary_component],
                    "summary": summary,
                    "description": description,
                },
                "captures": "parent_issue_key",
            }
        ]
    else:  # elevate
        description = compose_elevate_description(title, sections, components)
        confluence_body = strip_h1_and_marker(draft_text)
        actions = [
            {
                "step": 1,
                "tool": "atlassian.createConfluencePage",
                "args": {
                    "spaceKey": confluence_space,
                    "title": f"PRD: {title}",
                    "body": confluence_body,
                },
                "captures": "confluence_page_url",
            },
            {
                "step": 2,
                "tool": "atlassian.createJiraIssue",
                "args": {
                    "projectKey": project_key,
                    "issueType": issue_type,
                    "components": [primary_component],
                    "summary": summary,
                    "description": description,
                },
                "captures": "parent_issue_key",
                "substitutions": {
                    CONFLUENCE_PLACEHOLDER: "{{confluence_page_url}}",
                },
            },
            {
                "step": 3,
                "tool": "atlassian.addConfluenceRemoteLinkToJiraIssue",
                "args": {
                    "issueKey": "{{parent_issue_key}}",
                    "url": "{{confluence_page_url}}",
                    "title": f"PRD: {title}",
                },
            },
        ]

    return {
        "mode": mode,
        "summary": summary,
        "summary_exceeds_soft_limit": len(summary) > SUMMARY_SOFT_LIMIT,
        "issue_type": issue_type,
        "primary_component": primary_component,
        "candidate_components": components,
        "requires_user_choice": requires_user_choice,
        "actions": actions,
    }


# ----- CLI -----


def _resolve_mode(args: argparse.Namespace) -> str:
    if args.decision_json:
        try:
            payload = json.loads(args.decision_json.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            raise InvalidDraft(f"could not read --decision-json: {exc}") from exc
        decision = payload.get("decision")
        if decision not in {"inline", "elevate"}:
            raise InvalidDraft(
                f"--decision-json missing valid 'decision' field: got {decision!r}"
            )
        if args.mode and args.mode != decision:
            raise InvalidDraft(
                f"--mode={args.mode!r} conflicts with --decision-json decision={decision!r}"
            )
        return decision
    if not args.mode:
        raise InvalidDraft("must provide --decision-json or --mode")
    return args.mode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--draft", type=Path, required=True, help="Path to draft markdown.")
    parser.add_argument(
        "--decision-json",
        type=Path,
        default=None,
        help="JSON output from significance_check.py.",
    )
    parser.add_argument(
        "--mode", choices=["inline", "elevate"], default=None, help="Override mode."
    )
    parser.add_argument("--project-key", default="PLTPM", help="Jira project key.")
    parser.add_argument(
        "--confluence-space", default="PS", help="Confluence space key for elevated PRDs."
    )
    args = parser.parse_args(argv)

    try:
        draft_text = args.draft.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    try:
        mode = _resolve_mode(args)
        result = plan(
            draft_text,
            mode=mode,
            project_key=args.project_key,
            confluence_space=args.confluence_space,
        )
    except InvalidDraft as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
