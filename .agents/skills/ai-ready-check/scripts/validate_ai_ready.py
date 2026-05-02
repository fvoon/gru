"""Validate a child Jira ticket against the gru `ai-ready` contract.

Multi-phase state machine. The LLM in chat invokes this script repeatedly with
progressively more fixtures attached; the script tells the LLM what to fetch
next via JSON action plans on stdout, until the script emits a terminal
publish-or-no-op plan.

Phase ladder (each step exits 0 with the named status until terminal):

  needs_fetch_ticket            --ticket-fixture not provided yet. Emits a
                                getJiraIssue call (with comments + issuelinks
                                expanded) so we can see status, components,
                                links, and prior ai-ready-check comments.

  needs_fetch_blockers          Ticket has `is blocked by` links and at least
                                one blocker fixture is missing. Emits one
                                getJiraIssue per missing blocker key.

  ai_ready                      Terminal. All 6 checks pass AND the prior
                                ai-ready-check outcome (if any) differs from
                                the current. Action plan: addComment +
                                editJiraIssue (label add).

  not_ai_ready                  Terminal. At least 1 check failed AND the
                                outcome differs from the prior. Action plan:
                                addComment + editJiraIssue (label remove,
                                if currently set).

  no_change                     Terminal. Outcome matches the prior
                                ai-ready-check comment AND the label state
                                already matches. Empty action plan; surfaces
                                that re-runs are no-ops.

  invalid_input                 Exits 1. Payload is not a valid Jira issue
                                shape (e.g., missing `key`, `description` is
                                ADF instead of markdown, etc.).

Pure script: no MCP calls, no network, no clock. The LLM is the executor; this
script is a deterministic compute layer that emits action plans.
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

# `markdown` is the shared helper module; `noqa: E402` because we have to do path
# setup above the import.
from markdown import split_top_level_sections  # noqa: E402

# ----- constants (sentinel-tested against .agents/jira-conventions.md) -----


# Locked at sentinel test (Issue 3C): `CANONICAL_COMPONENTS` and
# `AI_READY_LABEL` MUST appear verbatim in the conventions doc.
CANONICAL_COMPONENTS = (
    "payment-platform",
    "walletapi",
    "infrastructure",
    "spring-boot-starters",
)
AI_READY_LABEL = "ai-ready"

# NOT sentinel-locked (Issue 3C): trusting Atlassian-stable globals + gru
# workflow names. Drift here surfaces at runtime against PLTPM, which is
# acceptable per the locked decision.
ALLOWED_ISSUE_TYPES = ("Task", "Technical Story")
REQUIRED_STATUS = "next"
IMPLEMENT_LINK_TYPE = "Implement"
BLOCKER_LINK_TYPE = "Blocks"
DONE_STATUS = "Done"

# Comment marker for the outcome-diff (Issue 4B). Versioned so future format
# bumps don't break old comments.
COMMENT_MARKER = "<!-- ai-ready-check:v1 -->"
COMMENT_OUTCOME_RE = re.compile(r"<!-- ai-ready-outcome: (?P<outcome>\{.*?\}) -->")

# AC section heading the description must contain (Issue 2D).
AC_SECTION_HEADING = "Acceptance criteria"

# Order of checks in the outcome dict — keep stable so JSON keys serialize
# predictably for the byte-stable goldens.
CHECK_KEYS = (
    "ac_present",
    "blockers_done",
    "components_canonical",
    "implement_link",
    "status_next",
    "issue_type_allowed",
)

# Human-readable labels used in the rendered failure comment. Keys mirror
# CHECK_KEYS so iteration order is stable.
CHECK_LABELS = {
    "ac_present": "Acceptance criteria section",
    "blockers_done": "Blockers all Done",
    "components_canonical": "Component is canonical (exactly 1)",
    "implement_link": "Implement-link to a parent",
    "status_next": "Status is `next`",
    "issue_type_allowed": "Issue type is Task / Technical Story",
}


# ----- exceptions -----


class InvalidInput(Exception):
    """Bad ticket / blocker fixture (exit 1)."""


# ----- payload extraction helpers -----


def _extract_field(payload: dict, field: str, default=None):
    """Read `payload.fields[field]` defensively; tolerate the field at top level.

    Atlassian REST payloads put most fields under `fields`; some MCP wrappers
    flatten common ones to top-level. We accept both.
    """
    fields = payload.get("fields") or {}
    if isinstance(fields, dict) and field in fields:
        return fields[field]
    return payload.get(field, default)


def _name_of(value, default: str = "") -> str:
    """Read `.name` from a {name: ...} dict; fall back to str(value); tolerate None."""
    if value is None:
        return default
    if isinstance(value, dict):
        return str(value.get("name") or default)
    return str(value)


def normalize_ticket(payload: dict) -> dict:
    """Flatten an `atlassian.getJiraIssue` payload for a child ticket.

    Returned dict:
      - key, summary, description (markdown str)
      - issue_type (str), status (str)
      - components (list[str]), labels (list[str])
      - issuelinks (list[dict]) — left raw for downstream extraction
      - comments (list[str]) — body strings only; we only scan for the marker
    """
    if not isinstance(payload, dict):
        raise InvalidInput("ticket payload is not a JSON object")

    key = payload.get("key")
    if not key or not isinstance(key, str):
        raise InvalidInput("ticket payload missing `key`")

    summary = _extract_field(payload, "summary", "") or ""

    description = _extract_field(payload, "description", "") or ""
    if not isinstance(description, str):
        # Atlassian Cloud returns description as ADF (a dict) by default;
        # the agent must request `expand=renderedFields` or convert ADF to
        # markdown before passing it to this script.
        raise InvalidInput(
            f"ticket {key}: description is not a markdown string "
            f"(got {type(description).__name__}); the agent must convert "
            "ADF to markdown before invoking this script"
        )

    issue_type = _name_of(_extract_field(payload, "issuetype"))
    status = _name_of(_extract_field(payload, "status"))

    components_raw = _extract_field(payload, "components") or []
    if not isinstance(components_raw, list):
        components_raw = []
    components = [_name_of(c) for c in components_raw if c is not None]

    labels_raw = _extract_field(payload, "labels") or []
    if not isinstance(labels_raw, list):
        labels_raw = []
    labels = [str(label_value) for label_value in labels_raw if isinstance(label_value, str)]

    issuelinks_raw = _extract_field(payload, "issuelinks") or []
    issuelinks = issuelinks_raw if isinstance(issuelinks_raw, list) else []

    comment_block = _extract_field(payload, "comment") or {}
    if isinstance(comment_block, dict):
        comment_list = comment_block.get("comments") or []
    elif isinstance(comment_block, list):
        comment_list = comment_block
    else:
        comment_list = []

    comments: list[str] = []
    for entry in comment_list:
        if isinstance(entry, dict):
            body = entry.get("body", "")
            comments.append(body if isinstance(body, str) else "")
        elif isinstance(entry, str):
            comments.append(entry)

    return {
        "key": key,
        "summary": summary,
        "description": description,
        "issue_type": issue_type,
        "status": status,
        "components": components,
        "labels": labels,
        "issuelinks": issuelinks,
        "comments": comments,
    }


def normalize_blocker(payload: dict) -> dict:
    """Flatten an `atlassian.getJiraIssue` payload for a blocker (status only)."""
    if not isinstance(payload, dict):
        raise InvalidInput("blocker payload is not a JSON object")
    key = payload.get("key")
    if not key or not isinstance(key, str):
        raise InvalidInput("blocker payload missing `key`")
    status = _name_of(_extract_field(payload, "status"))
    return {"key": key, "status": status}


# ----- link extraction -----


def extract_blocker_keys(ticket: dict) -> list[str]:
    """Return ordered list of `is blocked by` blocker keys.

    Atlassian convention: a child ticket is "blocked by" something means the
    `Blocks` link type with this ticket as the OUTWARD direction (i.e., the
    other ticket "blocks" this one). On the ticket's payload, the blocker
    appears under `inwardIssue`.

    Defensive: also accepts ticket payloads that put the blocker on
    `outwardIssue` (some MCP fixtures normalize differently). We disambiguate
    by checking the `inward` / `outward` text on the link's `type`.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for link in ticket.get("issuelinks", []):
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        type_name = _name_of(link_type)
        if type_name != BLOCKER_LINK_TYPE:
            continue
        # The "is blocked by" direction is whichever slot has the blocker.
        # Standard Atlassian shape: type.inward = "is blocked by", and the
        # blocker key sits in `inwardIssue`. We accept the symmetric case
        # for tolerant fixtures.
        inward_text = link_type.get("inward", "") if isinstance(link_type, dict) else ""
        outward_text = link_type.get("outward", "") if isinstance(link_type, dict) else ""

        candidate = None
        if "blocked by" in inward_text.lower() and isinstance(link.get("inwardIssue"), dict):
            candidate = link["inwardIssue"].get("key")
        elif "blocked by" in outward_text.lower() and isinstance(link.get("outwardIssue"), dict):
            candidate = link["outwardIssue"].get("key")
        else:
            # No directional hint — assume inward (standard) but only emit
            # if the inward slot is populated.
            if isinstance(link.get("inwardIssue"), dict):
                candidate = link["inwardIssue"].get("key")

        if isinstance(candidate, str) and candidate and candidate not in seen:
            seen.add(candidate)
            keys.append(candidate)
    return keys


def extract_implement_parent_key(ticket: dict) -> str | None:
    """Return the parent key linked via `Implement`, or None.

    On the child's payload, the parent typically appears under `outwardIssue`
    (the child "implements" the parent — outward direction). Defensive about
    both slots.
    """
    for link in ticket.get("issuelinks", []):
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        type_name = _name_of(link_type)
        if type_name != IMPLEMENT_LINK_TYPE:
            continue
        outward_text = link_type.get("outward", "") if isinstance(link_type, dict) else ""
        inward_text = link_type.get("inward", "") if isinstance(link_type, dict) else ""

        if outward_text.lower() == "implements" and isinstance(link.get("outwardIssue"), dict):
            key = link["outwardIssue"].get("key")
            if isinstance(key, str) and key:
                return key
        if inward_text.lower() == "is implemented by" and isinstance(link.get("inwardIssue"), dict):
            key = link["inwardIssue"].get("key")
            if isinstance(key, str) and key:
                return key
        # Defensive fallback: pick whichever slot is populated.
        for slot in ("outwardIssue", "inwardIssue"):
            target = link.get(slot)
            if isinstance(target, dict):
                key = target.get("key")
                if isinstance(key, str) and key:
                    return key
    return None


# ----- prior-outcome extraction (Issue 4B) -----


def extract_prior_outcome(comments: list[str]) -> dict | None:
    """Return the parsed outcome dict from the most recent ai-ready-check comment.

    Walks comments in reverse order (most recent last in Atlassian's payload),
    finds the first one whose body starts with `COMMENT_MARKER`, and parses
    the embedded JSON. Returns None if no marked comment is found OR the
    found comment has a malformed outcome (the latter is treated as "no
    prior" so that re-runs after a marker-format bump always emit a fresh
    comment instead of falsely short-circuiting).
    """
    for body in reversed(comments):
        if not isinstance(body, str):
            continue
        if not body.lstrip().startswith(COMMENT_MARKER):
            continue
        match = COMMENT_OUTCOME_RE.search(body)
        if not match:
            return None
        try:
            outcome = json.loads(match.group("outcome"))
        except json.JSONDecodeError:
            return None
        if not isinstance(outcome, dict):
            return None
        # Only the boolean check fields — defensively coerce to bool so a
        # malformed prior (e.g., null/number) doesn't masquerade as a match.
        coerced = {k: bool(outcome.get(k)) for k in CHECK_KEYS if k in outcome}
        # If keys are missing, treat as no prior — checklist evolved.
        if set(coerced.keys()) != set(CHECK_KEYS):
            return None
        return coerced
    return None


# ----- checklist items (1 = pass, 0 = fail per item) -----


def check_ac_present(ticket: dict) -> bool:
    """Issue 2D: `## Acceptance criteria` section present and non-empty."""
    sections = split_top_level_sections(ticket["description"])
    body = sections.get(AC_SECTION_HEADING, "")
    return bool(body and body.strip())


def check_blockers_done(blockers: list[dict]) -> bool:
    """All `is blocked by` blockers have status == Done.

    Vacuously true if there are no blockers. Caller is responsible for
    pairing the blocker fixtures with the keys extracted from the ticket.
    """
    return all(b.get("status") == DONE_STATUS for b in blockers)


def check_components_canonical(ticket: dict) -> bool:
    """Exactly one Component, in CANONICAL_COMPONENTS."""
    components = ticket.get("components", [])
    return len(components) == 1 and components[0] in CANONICAL_COMPONENTS


def check_implement_link(ticket: dict) -> bool:
    """At least one Implement link to a parent."""
    return extract_implement_parent_key(ticket) is not None


def check_status_next(ticket: dict) -> bool:
    """Status name == 'next'."""
    return ticket.get("status") == REQUIRED_STATUS


def check_issue_type_allowed(ticket: dict) -> bool:
    """Issue type is one of the allowed values."""
    return ticket.get("issue_type") in ALLOWED_ISSUE_TYPES


def run_checklist(ticket: dict, blockers: list[dict]) -> dict:
    """Run all 6 checks; return ordered outcome dict matching CHECK_KEYS."""
    return {
        "ac_present": check_ac_present(ticket),
        "blockers_done": check_blockers_done(blockers),
        "components_canonical": check_components_canonical(ticket),
        "implement_link": check_implement_link(ticket),
        "status_next": check_status_next(ticket),
        "issue_type_allowed": check_issue_type_allowed(ticket),
    }


# ----- comment body rendering -----


def _render_failure_detail(check_key: str, ticket: dict, blockers: list[dict]) -> str:
    """Per-check failure explanation. Specific to the check; lives here so the
    rendering logic isn't scattered across the script."""
    if check_key == "ac_present":
        return (
            "**Acceptance criteria section missing or empty.** Add a "
            f"`## {AC_SECTION_HEADING}` section to the ticket description "
            "with at least one checklist item."
        )
    if check_key == "blockers_done":
        not_done = [b for b in blockers if b.get("status") != DONE_STATUS]
        bits = ", ".join(f"`{b['key']}` ({b.get('status', '?')})" for b in not_done) or "(none)"
        return f"**Blockers not Done.** Still open: {bits}."
    if check_key == "components_canonical":
        components = ticket.get("components", [])
        canonical_list = ", ".join(f"`{c}`" for c in CANONICAL_COMPONENTS)
        if not components:
            return (
                f"**Component missing.** Set exactly one of: {canonical_list}."
            )
        if len(components) > 1:
            found = ", ".join(f"`{c}`" for c in components)
            return (
                f"**Multiple Components set ({found}).** gru-managed tickets "
                f"carry exactly one Component, and it must be one of: "
                f"{canonical_list}."
            )
        return (
            f"**Component `{components[0]}` is not canonical.** Use one of: "
            f"{canonical_list}."
        )
    if check_key == "implement_link":
        return (
            "**No Implement link to a parent.** Add an `Implement` link "
            "outward from this ticket to its parent Story / Technical Story."
        )
    if check_key == "status_next":
        current = ticket.get("status") or "(unknown)"
        return (
            f"**Status is `{current}`, expected `{REQUIRED_STATUS}`.** "
            "An engineer must transition this ticket `To Do → next` "
            "(transition id `221`) before it can be ai-ready."
        )
    if check_key == "issue_type_allowed":
        current = ticket.get("issue_type") or "(unknown)"
        allowed = ", ".join(f"`{t}`" for t in ALLOWED_ISSUE_TYPES)
        return (
            f"**Issue type is `{current}`, expected one of {allowed}.** "
            "`Research` and `Design` tickets are never ai-ready."
        )
    return f"**{check_key} failed.**"


def render_comment_body(
    checks: dict, ticket: dict, blockers: list[dict], passed: bool
) -> str:
    """Render the markdown body for the addCommentToJiraIssue action.

    Format (per locked Issue 4B):

        <!-- ai-ready-check:v1 -->
        <!-- ai-ready-outcome: {...stable JSON...} -->

        ## ai-ready-check passed | failed

        - bullets per failed check (only on fail)
    """
    # Outcome JSON — stable key order (CHECK_KEYS) so the marker stays
    # byte-stable across runs that produce the same outcome.
    outcome_json = json.dumps(
        {k: bool(checks[k]) for k in CHECK_KEYS},
        separators=(",", ":"),
        sort_keys=False,
    )

    header = (
        f"{COMMENT_MARKER}\n"
        f"<!-- ai-ready-outcome: {outcome_json} -->\n\n"
    )

    if passed:
        body = (
            "## ai-ready-check passed\n\n"
            "All 6 checks pass. The `ai-ready` label has been applied; this "
            "ticket is eligible for the dispatcher's pickup JQL "
            "(`status = next AND labels = ai-ready`)."
        )
    else:
        failed_keys = [k for k in CHECK_KEYS if not checks[k]]
        details = "\n".join(
            f"- {_render_failure_detail(k, ticket, blockers)}" for k in failed_keys
        )
        body = (
            "## ai-ready-check failed\n\n"
            f"{details}\n\n"
            "Re-run after addressing the items above. The `ai-ready` "
            "label has been removed (if it was set)."
        )
    return header + body


# ----- action plan emitters -----


def build_phase1_plan(ticket_key: str) -> dict:
    """needs_fetch_ticket — emit one getJiraIssue with comments + issuelinks."""
    return {
        "status": "needs_fetch_ticket",
        "ticket_key": ticket_key,
        "actions": [
            {
                "step": 1,
                "tool": "atlassian.getJiraIssue",
                "args": {
                    "issueKey": ticket_key,
                    "expand": "comments,issuelinks,renderedFields",
                },
                "purpose": (
                    "Fetch the child ticket plus comments (for prior "
                    "ai-ready-check outcome) and issuelinks (for blockers + "
                    "Implement parent verification)."
                ),
            }
        ],
        "next_step": (
            "Save the response to a JSON file and re-invoke this script with "
            "--ticket-fixture <path>."
        ),
    }


def build_phase2_plan(ticket_key: str, missing_blocker_keys: list[str]) -> dict:
    """needs_fetch_blockers — emit one getJiraIssue per missing blocker."""
    return {
        "status": "needs_fetch_blockers",
        "ticket_key": ticket_key,
        "blocker_keys": missing_blocker_keys,
        "actions": [
            {
                "step": i + 1,
                "tool": "atlassian.getJiraIssue",
                "args": {"issueKey": key},
                "purpose": (
                    f"Fetch blocker {key} so we can verify its status is Done."
                ),
            }
            for i, key in enumerate(missing_blocker_keys)
        ],
        "next_step": (
            "Save each response to a JSON file and re-invoke with "
            "--blocker-fixtures <path1> <path2> ..."
        ),
    }


def _label_action(ticket_key: str, mode: str) -> dict:
    """Build an editJiraIssue action for label add or remove.

    Uses Atlassian REST's `update` shape (`update.labels[{add|remove}]`) so
    the operation merges with existing labels instead of overwriting them.
    """
    if mode not in ("add", "remove"):
        raise ValueError(f"_label_action mode must be add|remove, got {mode!r}")
    return {
        "tool": "atlassian.editJiraIssue",
        "args": {
            "issueKey": ticket_key,
            "update": {"labels": [{mode: AI_READY_LABEL}]},
        },
        "purpose": (
            f"{'Apply' if mode == 'add' else 'Remove'} the `{AI_READY_LABEL}` "
            "label using the merge-safe REST `update` shape."
        ),
    }


def build_terminal_plan(
    ticket: dict,
    blockers: list[dict],
    checks: dict,
    prior_outcome: dict | None,
) -> dict:
    """Terminal phase — emit ai_ready / not_ai_ready / no_change.

    Outcome diff (Issue 4B):
      - If checks == prior_outcome AND label state already matches, emit
        no_change with empty actions.
      - Otherwise, emit a comment + label-edit action plan.
    """
    ticket_key = ticket["key"]
    passed = all(checks.values())
    has_label = AI_READY_LABEL in ticket.get("labels", [])
    desired_label_state = passed  # True means label should be present.
    label_state_correct = has_label == desired_label_state

    outcome_changed = prior_outcome != checks
    if not outcome_changed and label_state_correct:
        return {
            "status": "no_change",
            "ticket_key": ticket_key,
            "checks": checks,
            "prior_outcome": prior_outcome,
            "label_currently_set": has_label,
            "actions": [],
        }

    failed_items = [k for k in CHECK_KEYS if not checks[k]]
    body = render_comment_body(checks, ticket, blockers, passed=passed)

    actions: list[dict] = [
        {
            "step": 1,
            "tool": "atlassian.addCommentToJiraIssue",
            "args": {"issueKey": ticket_key, "body": body},
            "purpose": (
                "Post the ai-ready-check outcome with embedded marker so "
                "future re-runs can short-circuit on no_change."
            ),
        }
    ]

    # Label edit only when state needs to change. If outcome flipped to pass
    # and label is already set, that's still an outcome change (we want a
    # fresh comment) but we skip the redundant label add.
    if desired_label_state and not has_label:
        action = {"step": 2, **_label_action(ticket_key, "add")}
        actions.append(action)
    elif not desired_label_state and has_label:
        action = {"step": 2, **_label_action(ticket_key, "remove")}
        actions.append(action)

    payload: dict = {
        "status": "ai_ready" if passed else "not_ai_ready",
        "ticket_key": ticket_key,
        "checks": checks,
        "outcome_changed": outcome_changed,
        "label_currently_set": has_label,
        "actions": actions,
    }
    if not passed:
        payload["failed_items"] = failed_items
    return payload


# ----- driver -----


def _read_json(path: Path) -> dict | list:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise InvalidInput(f"failed to read {path}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInput(f"{path} is not valid JSON: {exc}") from exc


def run(args: argparse.Namespace) -> tuple[int, dict]:
    """Drive the phase machine. Returns (exit_code, payload)."""
    # Phase 1: need ticket fixture.
    if args.ticket_fixture is None:
        if not args.ticket_key:
            return (
                2,
                {
                    "status": "error",
                    "reason": (
                        "either --ticket-fixture or --ticket-key must be "
                        "provided to invoke this script"
                    ),
                },
            )
        return (0, build_phase1_plan(args.ticket_key))

    # Read + normalize the ticket fixture.
    try:
        raw_ticket = _read_json(args.ticket_fixture)
        if not isinstance(raw_ticket, dict):
            raise InvalidInput("ticket fixture root must be an object")
        ticket = normalize_ticket(raw_ticket)
    except InvalidInput as exc:
        return (1, {"status": "invalid_input", "reason": str(exc)})

    # If the caller passed --ticket-key, sanity-check it matches the fixture.
    if args.ticket_key and args.ticket_key != ticket["key"]:
        return (
            1,
            {
                "status": "invalid_input",
                "reason": (
                    f"--ticket-key {args.ticket_key!r} does not match the "
                    f"fixture's `key` ({ticket['key']!r})"
                ),
            },
        )

    # Phase 2: blockers.
    blocker_keys = extract_blocker_keys(ticket)
    blocker_fixtures = list(args.blocker_fixtures or [])

    if blocker_keys and not blocker_fixtures:
        return (0, build_phase2_plan(ticket["key"], blocker_keys))

    # Read all blocker fixtures.
    try:
        blockers_raw = [_read_json(p) for p in blocker_fixtures]
        blockers = [
            normalize_blocker(b) for b in blockers_raw if isinstance(b, dict)
        ]
    except InvalidInput as exc:
        return (1, {"status": "invalid_input", "reason": str(exc)})

    # Verify the supplied fixtures cover exactly the keys we expected.
    supplied_keys = {b["key"] for b in blockers}
    expected_keys = set(blocker_keys)
    if supplied_keys != expected_keys:
        missing = sorted(expected_keys - supplied_keys)
        extra = sorted(supplied_keys - expected_keys)
        bits = []
        if missing:
            bits.append(f"missing: {missing}")
        if extra:
            bits.append(f"unexpected: {extra}")
        return (
            1,
            {
                "status": "invalid_input",
                "reason": (
                    "blocker fixtures don't match the ticket's `is blocked by` "
                    "links (" + "; ".join(bits) + "); re-run with the right set "
                    "of fixtures or refresh the ticket fixture"
                ),
            },
        )

    # Reorder blockers to match the ticket's link order so failure messages
    # are deterministic.
    blockers_by_key = {b["key"]: b for b in blockers}
    ordered_blockers = [blockers_by_key[k] for k in blocker_keys]

    # Run checklist.
    checks = run_checklist(ticket, ordered_blockers)
    prior_outcome = extract_prior_outcome(ticket["comments"])

    return (0, build_terminal_plan(ticket, ordered_blockers, checks, prior_outcome))


# ----- CLI -----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--ticket-key",
        type=str,
        default=None,
        help=(
            "PLTPM child ticket key (e.g., PLTPM-21503). Required for phase 1; "
            "optional but recommended after that as a sanity-check against the "
            "fixture."
        ),
    )
    parser.add_argument(
        "--ticket-fixture",
        type=Path,
        default=None,
        help=(
            "Path to the saved `atlassian.getJiraIssue` response for the child "
            "ticket. If absent, the script emits a phase-1 fetch plan instead."
        ),
    )
    parser.add_argument(
        "--blocker-fixtures",
        type=Path,
        nargs="*",
        default=None,
        help=(
            "Paths to saved `atlassian.getJiraIssue` responses for each "
            "`is blocked by` blocker. Required iff the ticket has blockers; "
            "the set must exactly match the keys extracted from the ticket."
        ),
    )
    args = parser.parse_args(argv)

    code, payload = run(args)
    target = sys.stderr if code != 0 else sys.stdout
    print(json.dumps(payload, indent=2, ensure_ascii=False), file=target)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
