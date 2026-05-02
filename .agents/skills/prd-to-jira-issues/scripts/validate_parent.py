"""Validate that a Jira parent ticket is gru-PRD-shaped and ready for slicing.

Three-phase script (the LLM in chat orchestrates the phases):

  Phase 1 (no fixtures): emit a JSON action plan with `atlassian.getJiraIssue` so the
  caller can fetch the parent. Exits 0 with `status: "needs_fetch_parent"`.

  Phase 2 (parent fixture provided, parent is elevated, no Confluence fixture): emit
  an action plan with `atlassian.getConfluencePage` for the linked PRD page. Exits 0
  with `status: "needs_fetch_confluence"`.

  Phase 3 (parent fixture, plus Confluence fixture if elevated): validate the
  parent against gru conventions. Emits a normalized `parent.json` to stdout for
  `propose_slices.py` to consume. Exits 0 on success, 1 on validation failure
  (parent does not match conventions), 2 on IO / parse / inconsistency errors.

Validation gates (all must pass):
- issue type is `Story` or `Technical Story`
- description (or Confluence body if elevated) contains all 8 required PRD sections
- `components` is a list with exactly one entry, and that entry is one of the four
  canonical repos in `.agents/jira-conventions.md`

The script never calls Atlassian itself; the LLM is the executor.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Shared markdown helpers live in `.agents/skills/_lib/` (sibling of this skill).
SCRIPT_DIR = Path(__file__).resolve().parent
_LIB_PATH = SCRIPT_DIR.parent.parent / "_lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

from markdown import split_top_level_sections  # noqa: E402

# Must stay in lockstep with .agents/jira-conventions.md and write-a-prd's
# write_jira_prd.py. The sentinel test in tests/test_validate_parent.py asserts
# parity against the conventions file at runtime.
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
ALLOWED_PARENT_TYPES = frozenset({"Story", "Technical Story"})

# write-a-prd's elevate-mode description starts with this exact lead-in (see
# write_jira_prd.compose_elevate_description). We detect elevation by looking for
# either this string OR a Confluence URL in the description.
ELEVATED_LEAD_IN = "**This is a significant PRD; the full text lives in Confluence.**"
_CONFLUENCE_URL_RE = re.compile(
    r"https?://[\w.-]+\.atlassian\.net/wiki/spaces/[\w/-]+/pages/\d+(?:/[\w-]*)?"
)


class InvalidParent(Exception):
    """Raised when the fetched parent fails a validation gate."""


# ----- detection -----


def is_elevated(description: str) -> bool:
    """True when the description signals the PRD body lives in Confluence."""
    if ELEVATED_LEAD_IN in description:
        return True
    return bool(_CONFLUENCE_URL_RE.search(description))


def extract_confluence_url(description: str) -> str | None:
    """Return the first Confluence URL found in the description, or None."""
    m = _CONFLUENCE_URL_RE.search(description)
    return m.group(0) if m else None


# ----- normalization + validation -----


def _extract_field(parent_payload: dict, field: str, default=None):
    """Read `parent.fields[field]` defensively; tolerate the field being at top level."""
    fields = parent_payload.get("fields") or {}
    if field in fields:
        return fields[field]
    return parent_payload.get(field, default)


def normalize_parent(parent_payload: dict, confluence_body: str | None) -> dict:
    """Normalize an `atlassian.getJiraIssue` payload + optional Confluence body.

    Returns a flat dict with the keys downstream scripts care about:
      - key, summary, issue_type, components, description (raw),
        full_prd_text (Confluence body if elevated, else description),
        is_elevated, confluence_url (if any).
    """
    key = parent_payload.get("key", "")
    summary = _extract_field(parent_payload, "summary", "")
    issue_type_raw = _extract_field(parent_payload, "issuetype") or {}
    if isinstance(issue_type_raw, dict):
        issue_type = issue_type_raw.get("name", "")
    else:
        issue_type = str(issue_type_raw)
    components_raw = _extract_field(parent_payload, "components") or []
    components = [
        c.get("name", "") if isinstance(c, dict) else str(c)
        for c in components_raw
    ]
    description = _extract_field(parent_payload, "description") or ""
    if not isinstance(description, str):
        # Defensively handle ADF (Atlas Doc Format) — caller should normalize, but if
        # they pass ADF anyway we fail loud rather than silently mis-parsing.
        raise InvalidParent(
            "description is not a markdown string; ADF must be normalized to markdown "
            "by the caller before passing to validate_parent.py"
        )

    elevated = is_elevated(description)
    full_prd = confluence_body if (elevated and confluence_body) else description

    return {
        "key": key,
        "summary": summary,
        "issue_type": issue_type,
        "components": components,
        "description": description,
        "full_prd_text": full_prd,
        "is_elevated": elevated,
        "confluence_url": extract_confluence_url(description) if elevated else None,
    }


def validate(normalized: dict) -> dict:
    """Apply all validation gates. Raises InvalidParent on any failure.

    Returns the normalized parent enriched with a `status: "ok"` field on success.
    """
    failures: list[str] = []

    if normalized["issue_type"] not in ALLOWED_PARENT_TYPES:
        failures.append(
            f"issue_type {normalized['issue_type']!r} not in {sorted(ALLOWED_PARENT_TYPES)}; "
            "gru parents must be Story or Technical Story"
        )

    components = normalized["components"]
    canonical_present = [c for c in components if c in CANONICAL_COMPONENTS]
    if len(canonical_present) != 1:
        failures.append(
            f"expected exactly one canonical Component on the parent; got "
            f"{components} (canonical present: {canonical_present})"
        )

    sections = split_top_level_sections(normalized["full_prd_text"])
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        failures.append(
            f"required PRD section(s) missing: {', '.join('## ' + s for s in missing)}"
        )

    if failures:
        raise InvalidParent("; ".join(failures))

    out = dict(normalized)
    out["status"] = "ok"
    out["primary_component"] = canonical_present[0]
    out["sections"] = {s: sections[s] for s in REQUIRED_SECTIONS}
    return out


# ----- action-plan emitters -----


def emit_phase1_plan(parent_key: str) -> dict:
    """Phase 1: ask the caller to fetch the parent issue."""
    return {
        "status": "needs_fetch_parent",
        "actions": [
            {
                "step": 1,
                "tool": "atlassian.getJiraIssue",
                "args": {"issueKey": parent_key},
                "purpose": (
                    "Fetch the parent ticket so validate_parent.py can inspect "
                    "issue type, Components, and description."
                ),
            }
        ],
        "next_step": (
            "Save the response to a JSON file and re-invoke: "
            "validate_parent.py --parent-key <KEY> --parent-fixture <path>."
        ),
    }


def emit_phase2_plan(confluence_url: str) -> dict:
    """Phase 2: parent is elevated; ask for the Confluence body."""
    return {
        "status": "needs_fetch_confluence",
        "confluence_url": confluence_url,
        "actions": [
            {
                "step": 1,
                "tool": "atlassian.getConfluencePage",
                "args": {"url": confluence_url},
                "purpose": (
                    "Parent is elevated (Confluence-linked). Fetch the page body "
                    "so validate_parent.py can validate the full PRD against the "
                    "8-section schema."
                ),
            }
        ],
        "next_step": (
            "Save the response (must include a markdown `body` field) to a JSON file "
            "and re-invoke validate_parent.py with --confluence-fixture <path>."
        ),
    }


# ----- CLI -----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--parent-key",
        required=True,
        help="Parent issue key, e.g. PLTPM-21500.",
    )
    parser.add_argument(
        "--parent-fixture",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file containing the response from "
            "atlassian.getJiraIssue. If absent, emits the phase-1 fetch plan."
        ),
    )
    parser.add_argument(
        "--confluence-fixture",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file containing the Confluence page body for an "
            "elevated parent. Required when the parent is elevated."
        ),
    )
    args = parser.parse_args(argv)

    if args.parent_fixture is None:
        print(json.dumps(emit_phase1_plan(args.parent_key), indent=2))
        return 0

    try:
        parent_payload = json.loads(args.parent_fixture.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(
            json.dumps({"status": "error", "reason": f"parent fixture is not valid JSON: {exc}"}),
            file=sys.stderr,
        )
        return 2

    if not isinstance(parent_payload, dict):
        print(
            json.dumps({"status": "error", "reason": "parent fixture must be a JSON object"}),
            file=sys.stderr,
        )
        return 2

    try:
        confluence_body: str | None = None
        if args.confluence_fixture is not None:
            raw = args.confluence_fixture.read_text(encoding="utf-8")
            confluence_payload = json.loads(raw)
            if isinstance(confluence_payload, dict):
                confluence_body = confluence_payload.get("body")
            else:
                confluence_body = None
            if not isinstance(confluence_body, str):
                print(
                    json.dumps(
                        {
                            "status": "error",
                            "reason": "confluence fixture must contain a markdown `body` string",
                        }
                    ),
                    file=sys.stderr,
                )
                return 2

        normalized = normalize_parent(parent_payload, confluence_body)
    except InvalidParent as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    if normalized["is_elevated"] and confluence_body is None:
        url = normalized["confluence_url"] or "<unknown>"
        print(json.dumps(emit_phase2_plan(url), indent=2))
        return 0

    try:
        validated = validate(normalized)
    except InvalidParent as exc:
        print(
            json.dumps({"status": "invalid", "reason": str(exc), "parent": normalized})
        )
        return 1

    print(json.dumps(validated, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
