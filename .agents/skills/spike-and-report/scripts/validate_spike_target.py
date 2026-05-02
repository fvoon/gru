"""Validate a PLTPM Research ticket and orchestrate fetches for the spike-and-report skill.

Multi-phase state machine. The LLM in chat invokes this script repeatedly with
progressively more fixtures attached; the script tells the LLM what to fetch
next via JSON action plans on stdout, until everything needed is present and
the script emits a normalized `target.json` describing the spike target for
`bootstrap_worktree.py` to consume.

Phase ladder (each step exits 0 with the named status until the terminal `ok`):

  needs_conventions_bootstrap   `## Confluence` section in jira-conventions.md
                                missing or still has placeholder values.
                                (Exits 2; not part of normal happy path.)

  needs_fetch_research          --research-fixture not provided yet.

  invalid_issue_type            (Exits 1.) Research ticket fixture present but
                                type isn't `Research` (lenient on trailing
                                space). Terminal failure unless --force.

  already_done                  (Exits 2.) Research ticket is in `Done` status.
                                Override with --force.

  existing_confluence_link      (Exits 2.) A Confluence URL already appears in
                                the Research ticket's comments. Caller should
                                use `--update-existing` (handled in write
                                script) or `--force` to overwrite.

  existing_redline              (Exits 2.) `redlines/spike-<KEY>-report.md`
                                exists; pick up from there or delete + retry.

  existing_worktree             (Exits 2.) Worktree path already exists at
                                `<worktree-base>/spike/<KEY>/`. Use
                                `--reset-worktree` (in bootstrap script) or
                                `--force` here to ignore.

  needs_fetch_parent            Research is `Implement`-linked to a parent and
                                no --parent-fixture has been provided yet.

  needs_fetch_confluence        Parent is elevated (PRD lives in Confluence)
                                and no --confluence-fixture has been provided.

  needs_repo_selection          All Jira / Confluence fetches done but no
                                --repos was provided. Engineer must pick one
                                or more of the four canonical repos.

  needs_graphify_queries        Repos provided but --graphify-fixture not
                                provided yet. Emits a graphify query plan.

  ok                            All fixtures present; emits the normalized
                                target JSON for downstream scripts.

Pure script: no MCP calls, no network, no clock unless the agent passes one in.
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

# Must stay in lockstep with .agents/jira-conventions.md. The sentinel test
# in tests/test_validate_spike_target.py asserts parity at runtime.
CANONICAL_REPOS = (
    "payment-platform",
    "walletapi",
    "infrastructure",
    "spring-boot-starters",
)
RESEARCH_TYPE_LENIENT = "Research"
"""Internal canonical form (no trailing space). Atlassian-side type name carries
a trailing space (`Research `, id 10720); we accept both on input and re-add
the trailing space when writing to Atlassian."""

DONE_STATUS = "Done"
PLACEHOLDER_TBA = "<TBA — bootstrap>"

# Detect a Confluence URL anywhere in a string (description, comment body, ...).
# Mirrors prd-to-jira-issues' validate_parent.py — keep in sync.
_CONFLUENCE_URL_RE = re.compile(
    r"https?://[\w.-]+\.atlassian\.net/wiki/spaces/[\w/-]+/pages/\d+(?:/[\w-]*)?"
)

# Lead-in that write-a-prd uses for elevated PRD descriptions.
ELEVATED_LEAD_IN = "**This is a significant PRD; the full text lives in Confluence.**"

# Conventions-file regex for the `## Confluence` section's keyvalue rows.
# Tolerant of any leading-bullet variant, trailing whitespace, and (intentionally)
# captures the `<TBA — bootstrap>` placeholder so we can detect it.
_CONVENTIONS_KEY_RE = re.compile(
    r"^-\s+\*\*(?P<key>[^*]+?)\*\*[^:]*:\s*`(?P<value>[^`]+)`\s*$",
    re.MULTILINE,
)


# ----- exceptions -----


class ValidationError(Exception):
    """Terminal validation failure (exit 1)."""


class IdempotencyRefusal(Exception):
    """Idempotency-driven refusal (exit 2). Caller can override with --force."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class ConventionsError(Exception):
    """`## Confluence` section missing or unbootstrapped (exit 2)."""


# ----- conventions -----


def parse_confluence_section(conventions_text: str) -> dict:
    """Extract Confluence space + Spikes parent page id from jira-conventions.md.

    Returns a dict with keys `space_key`, `space_id`, `spikes_parent_page_id`.
    Raises `ConventionsError` if the section is missing or any value is still
    the `<TBA — bootstrap>` placeholder.
    """
    sections = split_top_level_sections(conventions_text)
    if "Confluence" not in sections:
        raise ConventionsError(
            "`## Confluence` section missing from .agents/jira-conventions.md; "
            "add it per the spike-and-report bootstrap checklist."
        )

    body = sections["Confluence"]
    matches = {
        m.group("key").strip(): m.group("value").strip()
        for m in _CONVENTIONS_KEY_RE.finditer(body)
    }

    out: dict[str, str] = {}
    required = (
        ("Confluence space key", "space_key"),
        ("Confluence space ID", "space_id"),
        ("Spikes parent page ID", "spikes_parent_page_id"),
    )
    missing: list[str] = []
    placeholders: list[str] = []
    for label, key in required:
        # Header keys may carry parenthetical clarifications, so we only require
        # the prefix to match.
        candidates = [v for k, v in matches.items() if k.startswith(label)]
        if not candidates:
            missing.append(label)
            continue
        value = candidates[0]
        if value == PLACEHOLDER_TBA:
            placeholders.append(label)
            continue
        out[key] = value

    if missing or placeholders:
        bits = []
        if missing:
            bits.append("missing keys: " + ", ".join(missing))
        if placeholders:
            bits.append("still placeholder: " + ", ".join(placeholders))
        raise ConventionsError(
            "`## Confluence` section in .agents/jira-conventions.md is not "
            "fully bootstrapped (" + "; ".join(bits) + "). "
            "Discover the values via the Atlassian MCP or the Confluence web UI "
            "and replace the `<TBA — bootstrap>` placeholders."
        )

    return out


# ----- research-ticket inspection -----


def _extract_field(payload: dict, field: str, default=None):
    """Read `payload.fields[field]` defensively; tolerate the field at top level."""
    fields = payload.get("fields") or {}
    if field in fields:
        return fields[field]
    return payload.get(field, default)


def normalize_research(payload: dict) -> dict:
    """Flatten an `atlassian.getJiraIssue` Research-ticket payload.

    Keys returned: key, summary, description, issue_type, status, comments
    (list of strings — we only need to scan for Confluence URLs), implement_parent_key
    (str or None).
    """
    key = payload.get("key", "")
    summary = _extract_field(payload, "summary", "") or ""
    description = _extract_field(payload, "description", "") or ""
    if not isinstance(description, str):
        raise ValidationError(
            "research-ticket description is not a markdown string; ADF must be "
            "normalized to markdown by the caller before passing to this script"
        )

    issue_type_raw = _extract_field(payload, "issuetype") or {}
    issue_type = (
        issue_type_raw.get("name", "")
        if isinstance(issue_type_raw, dict)
        else str(issue_type_raw)
    )

    status_raw = _extract_field(payload, "status") or {}
    status_name = (
        status_raw.get("name", "")
        if isinstance(status_raw, dict)
        else str(status_raw)
    )

    comment_block = _extract_field(payload, "comment") or {}
    if isinstance(comment_block, dict):
        comment_list = comment_block.get("comments") or []
    elif isinstance(comment_block, list):
        comment_list = comment_block
    else:
        comment_list = []

    comments: list[str] = []
    for c in comment_list:
        if isinstance(c, dict):
            body = c.get("body", "")
            comments.append(body if isinstance(body, str) else "")
        elif isinstance(c, str):
            comments.append(c)

    implement_parent_key = _find_implement_parent(payload)

    return {
        "key": key,
        "summary": summary,
        "description": description,
        "issue_type": issue_type,
        "status": status_name,
        "comments": comments,
        "implement_parent_key": implement_parent_key,
    }


def _find_implement_parent(payload: dict) -> str | None:
    """Return the issue key of the parent linked via `Implement` (inward), or None.

    The Atlassian convention (per .agents/jira-conventions.md "createIssueLink
    directionality"): `inwardIssue` is the parent, `outwardIssue` is the child;
    so on the child's payload we want `Implement` links whose direction shows
    the child `implements` an inward parent.
    """
    links = _extract_field(payload, "issuelinks") or []
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        type_name = (
            link_type.get("name", "")
            if isinstance(link_type, dict)
            else str(link_type)
        )
        if type_name != "Implement":
            continue
        # The Research ticket "implements" its parent. From the child's
        # perspective the parent appears under `inwardIssue` (the link reads
        # "is implemented by" inward and "implements" outward; on the child's
        # side the parent is the inward direction, but Atlassian payloads use
        # `outwardIssue` to describe the *outward* target relative to *this*
        # issue. We have to be defensive about both shapes since fixtures
        # vary).
        for slot in ("outwardIssue", "inwardIssue"):
            target = link.get(slot)
            if isinstance(target, dict):
                tkey = target.get("key")
                if tkey:
                    return tkey
    return None


def is_research_type(issue_type: str) -> bool:
    """True if the issue type is `Research` (lenient on the Atlassian trailing space)."""
    return issue_type.strip() == RESEARCH_TYPE_LENIENT


def is_done(status: str) -> bool:
    return status.strip().lower() == DONE_STATUS.lower()


def find_confluence_in_comments(comments: list[str]) -> str | None:
    """Return the first Confluence URL found in any comment body, or None."""
    for body in comments:
        m = _CONFLUENCE_URL_RE.search(body or "")
        if m:
            return m.group(0)
    return None


# ----- parent-PRD inspection -----


def is_parent_elevated(parent_payload: dict) -> tuple[bool, str | None]:
    """Mirrors prd-to-jira-issues' `is_elevated`. Returns `(elevated, confluence_url)`."""
    description = _extract_field(parent_payload, "description") or ""
    if not isinstance(description, str):
        return (False, None)
    if ELEVATED_LEAD_IN in description:
        m = _CONFLUENCE_URL_RE.search(description)
        return (True, m.group(0) if m else None)
    m = _CONFLUENCE_URL_RE.search(description)
    if m:
        return (True, m.group(0))
    return (False, None)


# ----- filesystem idempotency checks -----


def existing_redline_path(redline_dir: Path, research_key: str) -> Path | None:
    """Return the path to `redlines/spike-<KEY>-report.md` if it exists, else None."""
    candidate = redline_dir / f"spike-{research_key}-report.md"
    return candidate if candidate.exists() else None


def existing_worktree_path(worktree_base: Path, research_key: str) -> Path | None:
    """Return `<worktree-base>/spike/<KEY>` if it exists, else None.

    The spike worktree layout is `<worktree-base>/spike/<KEY>/<repo>/`; we
    consider any non-empty `<worktree-base>/spike/<KEY>` directory a refusal
    trigger, so re-running over an in-progress spike doesn't clobber it.
    """
    candidate = worktree_base / "spike" / research_key
    if candidate.exists() and any(candidate.iterdir()):
        return candidate
    return None


# ----- repo selection -----


def parse_repo_list(raw: str) -> list[str]:
    """Split a comma-separated list of repo names; validate each is canonical.

    Returns the list in the order given (preserves engineer's intended order
    so the eventual report shows repos in the order they were named).
    """
    repos = [r.strip() for r in raw.split(",") if r.strip()]
    if not repos:
        raise ValidationError("--repos must be a non-empty comma-separated list")
    invalid = [r for r in repos if r not in CANONICAL_REPOS]
    if invalid:
        raise ValidationError(
            f"--repos contains non-canonical repo(s): {invalid}. "
            f"Allowed: {list(CANONICAL_REPOS)}"
        )
    seen: set[str] = set()
    deduped: list[str] = []
    for r in repos:
        if r not in seen:
            deduped.append(r)
            seen.add(r)
    return deduped


# ----- action-plan emitters -----


def emit_phase_fetch_research(research_key: str) -> dict:
    return {
        "status": "needs_fetch_research",
        "actions": [
            {
                "step": 1,
                "tool": "atlassian.getJiraIssue",
                "args": {
                    "issueKey": research_key,
                    "fields": ["summary", "description", "issuetype", "status",
                               "components", "issuelinks", "comment"],
                },
                "purpose": (
                    "Fetch the Research ticket so validate_spike_target.py can "
                    "check the issue type, status, and Implement-link parent."
                ),
            }
        ],
        "next_step": (
            "Save the response to a JSON file and re-invoke: "
            "validate_spike_target.py --research-key <KEY> --research-fixture <path>."
        ),
    }


def emit_phase_fetch_parent(parent_key: str) -> dict:
    return {
        "status": "needs_fetch_parent",
        "actions": [
            {
                "step": 1,
                "tool": "atlassian.getJiraIssue",
                "args": {
                    "issueKey": parent_key,
                    "fields": ["summary", "description", "issuetype"],
                },
                "purpose": (
                    "Fetch the Implement-linked parent so we can determine "
                    "whether its PRD is elevated to Confluence (which becomes "
                    "the spike sub-page parent)."
                ),
            }
        ],
        "next_step": (
            "Save the response to a JSON file and re-invoke with --parent-fixture <path>."
        ),
    }


def emit_phase_fetch_confluence(confluence_url: str) -> dict:
    return {
        "status": "needs_fetch_confluence",
        "confluence_url": confluence_url,
        "actions": [
            {
                "step": 1,
                "tool": "atlassian.getConfluencePage",
                "args": {"url": confluence_url},
                "purpose": (
                    "Parent PRD is elevated; fetch the Confluence page so we "
                    "can record its id as the spike sub-page parent."
                ),
            }
        ],
        "next_step": (
            "Save the response (must include `id`) to a JSON file and re-invoke "
            "with --confluence-fixture <path>."
        ),
    }


def emit_phase_select_repos() -> dict:
    return {
        "status": "needs_repo_selection",
        "prompt": (
            "Which repo(s) is this spike rooted in? Answer with one or more of "
            f"{list(CANONICAL_REPOS)}, comma-separated."
        ),
        "allowed_repos": list(CANONICAL_REPOS),
        "next_step": (
            "Re-invoke with --repos <repo>[,<repo>...] once the engineer answers."
        ),
    }


def emit_phase_graphify(research: dict, repos: list[str]) -> dict:
    """Phase: tell the LLM which graphify queries to run.

    Search-text seed = Research ticket summary + description (truncated). The
    repos list scopes which subset of the merged corpus matters; downstream
    scripts will keep only modules whose `repo` is in this list.
    """
    seed = (research.get("summary", "") + "\n\n" + research.get("description", ""))[:2000]
    return {
        "status": "needs_graphify_queries",
        "actions": [
            {
                "step": 1,
                "tool": "graphify.search",
                "args": {"text": seed},
                "purpose": (
                    "Search the codebase graph for modules / nodes related to "
                    "the Research ticket's question. Hits scoped to the spike "
                    "repos provide the 'existing state' the report cites."
                ),
            },
            {
                "step": 2,
                "tool": "graphify.get_node",
                "args": {"id": "<PER_HIT>"},
                "purpose": (
                    "Fetch metadata per hit so the briefing can name god nodes, "
                    "communities, and surrounding modules."
                ),
            },
            {
                "step": 3,
                "tool": "graphify.get_edges",
                "args": {"from_id": "<EACH_HIT>"},
                "purpose": (
                    "Fetch outbound edges per hit so the briefing can describe "
                    "the connectedness of the area being spiked."
                ),
            },
        ],
        "scoped_repos": repos,
        "next_step": (
            "Aggregate the graphify responses into a JSON object with keys "
            "{nodes: [{name, repo, kind, ...}], edges: [{from, to, type}], "
            "communities: [{name, members}]} and re-invoke with "
            "--graphify-fixture <path>."
        ),
    }


def emit_target(
    research: dict,
    parent_key: str | None,
    confluence_parent_page_id: str,
    confluence_parent_path: str,
    repos: list[str],
    graphify: dict,
    spikes_parent_page_id: str,
    space_key: str,
    space_id: str,
) -> dict:
    """Terminal phase: normalized JSON for bootstrap_worktree.py."""
    return {
        "status": "ok",
        "research_key": research["key"],
        "research_summary": research["summary"],
        "research_question": research["description"],
        "parent_key": parent_key,
        "confluence_parent_page_id": confluence_parent_page_id,
        "confluence_parent_path": confluence_parent_path,
        "confluence_space_key": space_key,
        "confluence_space_id": space_id,
        "spikes_parent_page_id": spikes_parent_page_id,
        "repos": repos,
        "graphify": graphify,
    }


# ----- driver -----


def _load_json(path: Path, label: str) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise ValidationError(f"failed to read {label} at {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{label} at {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValidationError(f"{label} must be a JSON object; got {type(payload).__name__}")
    return payload


def run(args: argparse.Namespace) -> tuple[int, dict | str]:
    """Pure driver. Returns (exit_code, payload). Payload is a dict to print as
    JSON, or a stderr-only string for hard errors."""
    # 1. Conventions guard — always first.
    try:
        conventions_text = args.conventions_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        return (2, {"status": "needs_conventions_bootstrap", "reason": str(exc)})
    try:
        conventions = parse_confluence_section(conventions_text)
    except ConventionsError as exc:
        return (2, {"status": "needs_conventions_bootstrap", "reason": str(exc)})

    # 2. Filesystem-level idempotency (cheap; doesn't need a fixture).
    if not args.force:
        red = existing_redline_path(args.redline_dir, args.research_key)
        if red is not None:
            return (
                2,
                {
                    "status": "existing_redline",
                    "reason": (
                        f"redline file already exists at {red}; pick up from "
                        "there or delete it before re-running"
                    ),
                    "path": str(red),
                },
            )
        wt = existing_worktree_path(args.worktree_base, args.research_key)
        if wt is not None:
            return (
                2,
                {
                    "status": "existing_worktree",
                    "reason": (
                        f"worktree path already exists at {wt}; pass "
                        "--reset-worktree to bootstrap_worktree.py to nuke + "
                        "recreate, or --force here to ignore"
                    ),
                    "path": str(wt),
                },
            )

    # 3. Research fixture.
    if args.research_fixture is None:
        return (0, emit_phase_fetch_research(args.research_key))

    try:
        research_payload = _load_json(args.research_fixture, "research fixture")
    except ValidationError as exc:
        return (2, {"status": "error", "reason": str(exc)})

    try:
        research = normalize_research(research_payload)
    except ValidationError as exc:
        return (2, {"status": "error", "reason": str(exc)})

    # 4. Issue-type validation.
    if not is_research_type(research["issue_type"]):
        return (
            1,
            {
                "status": "invalid_issue_type",
                "reason": (
                    f"issue type {research['issue_type']!r} is not Research; "
                    "spike-and-report only operates on Research tickets"
                ),
                "research_key": research["key"],
            },
        )

    # 5. Comment-based + status-based idempotency.
    if not args.force:
        existing_link = find_confluence_in_comments(research["comments"])
        if existing_link is not None:
            return (
                2,
                {
                    "status": "existing_confluence_link",
                    "reason": (
                        f"Research ticket {research['key']} already has a "
                        f"Confluence link in its comments: {existing_link}. "
                        "Use --update-existing on write_spike_report.py to "
                        "overwrite the existing page, or --force here to "
                        "ignore."
                    ),
                    "research_key": research["key"],
                    "existing_url": existing_link,
                },
            )
        if is_done(research["status"]):
            return (
                2,
                {
                    "status": "already_done",
                    "reason": (
                        f"Research ticket {research['key']} is already in "
                        f"status {research['status']!r}; the spike has shipped"
                    ),
                    "research_key": research["key"],
                },
            )

    # 6. Parent resolution.
    parent_key = research["implement_parent_key"]
    confluence_parent_page_id: str
    confluence_parent_path: str

    if parent_key is None:
        confluence_parent_page_id = conventions["spikes_parent_page_id"]
        confluence_parent_path = "orphan"
    else:
        if args.parent_fixture is None:
            return (0, emit_phase_fetch_parent(parent_key))
        try:
            parent_payload = _load_json(args.parent_fixture, "parent fixture")
        except ValidationError as exc:
            return (2, {"status": "error", "reason": str(exc)})

        elevated, confluence_url = is_parent_elevated(parent_payload)
        if elevated:
            if args.confluence_fixture is None:
                if confluence_url is None:
                    return (
                        2,
                        {
                            "status": "error",
                            "reason": (
                                "parent PRD is flagged elevated but its "
                                "description has no Confluence URL we could "
                                "extract; fix the parent ticket or pass "
                                "--confluence-fixture explicitly"
                            ),
                        },
                    )
                return (0, emit_phase_fetch_confluence(confluence_url))
            try:
                confluence_payload = _load_json(args.confluence_fixture, "confluence fixture")
            except ValidationError as exc:
                return (2, {"status": "error", "reason": str(exc)})

            page_id = confluence_payload.get("id")
            if not isinstance(page_id, str) or not page_id.strip():
                return (
                    2,
                    {
                        "status": "error",
                        "reason": (
                            "confluence fixture is missing a non-empty `id` "
                            "field; spike-and-report needs the Confluence "
                            "page id to set the sub-page parent"
                        ),
                    },
                )
            confluence_parent_page_id = page_id
            confluence_parent_path = "elevated-parent"
        else:
            confluence_parent_page_id = conventions["spikes_parent_page_id"]
            confluence_parent_path = "inline-parent"

    # 7. Repo selection.
    if args.repos is None:
        return (0, emit_phase_select_repos())
    try:
        repos = parse_repo_list(args.repos)
    except ValidationError as exc:
        return (1, {"status": "invalid_repos", "reason": str(exc)})

    # 8. Graphify fixture.
    if args.graphify_fixture is None:
        return (0, emit_phase_graphify(research, repos))
    try:
        graphify = _load_json(args.graphify_fixture, "graphify fixture")
    except ValidationError as exc:
        return (2, {"status": "error", "reason": str(exc)})

    # 9. Terminal: emit normalized target.
    return (
        0,
        emit_target(
            research=research,
            parent_key=parent_key,
            confluence_parent_page_id=confluence_parent_page_id,
            confluence_parent_path=confluence_parent_path,
            repos=repos,
            graphify=graphify,
            spikes_parent_page_id=conventions["spikes_parent_page_id"],
            space_key=conventions["space_key"],
            space_id=conventions["space_id"],
        ),
    )


# ----- CLI -----


def _default_conventions_path() -> Path:
    """Walk up to find `.agents/jira-conventions.md` from the script's location."""
    return SCRIPT_DIR.parent.parent.parent / "jira-conventions.md"


def _default_redline_dir() -> Path:
    """`<skill-root>/redlines/`."""
    return SCRIPT_DIR.parent / "redlines"


def _default_worktree_base() -> Path:
    """`~/IdeaProjects/`. Spikes go to `<worktree-base>/spike/<KEY>/<repo>/`."""
    return Path.home() / "IdeaProjects"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--research-key",
        required=True,
        help="Research issue key, e.g. PLTPM-21500.",
    )
    parser.add_argument(
        "--research-fixture",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file containing the response from "
            "atlassian.getJiraIssue for the Research ticket."
        ),
    )
    parser.add_argument(
        "--parent-fixture",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file containing the response from "
            "atlassian.getJiraIssue for the Implement-linked parent ticket."
        ),
    )
    parser.add_argument(
        "--confluence-fixture",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file containing the response from "
            "atlassian.getConfluencePage for the elevated parent's PRD page. "
            "Required when the parent is elevated."
        ),
    )
    parser.add_argument(
        "--graphify-fixture",
        type=Path,
        default=None,
        help=(
            "Path to a JSON file with aggregated graphify query results. "
            "Required for the terminal `ok` phase."
        ),
    )
    parser.add_argument(
        "--repos",
        type=str,
        default=None,
        help=(
            "Comma-separated repo list (one or more of payment-platform, "
            "walletapi, infrastructure, spring-boot-starters)."
        ),
    )
    parser.add_argument(
        "--conventions-path",
        type=Path,
        default=_default_conventions_path(),
        help="Path to .agents/jira-conventions.md (defaults to the in-repo location).",
    )
    parser.add_argument(
        "--redline-dir",
        type=Path,
        default=_default_redline_dir(),
        help=(
            "Where redline files live (used for the existing-redline idempotency "
            "check). Defaults to <skill>/redlines/."
        ),
    )
    parser.add_argument(
        "--worktree-base",
        type=Path,
        default=_default_worktree_base(),
        help=(
            "Where spike worktrees live; existence at <base>/spike/<KEY>/ "
            "triggers the existing-worktree refusal."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip idempotency refusals (existing redline / worktree / Confluence link / Done).",
    )
    args = parser.parse_args(argv)

    code, payload = run(args)
    if isinstance(payload, dict):
        # Validation-failure shapes are also JSON, but we want them on stderr so
        # the LLM doesn't try to parse them as a fetch plan when piping stdout
        # to a file. Hard errors (exit 2) and validation refusals (exit 1) go
        # to stderr; informational phases (exit 0) go to stdout.
        target = sys.stderr if code != 0 else sys.stdout
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=target)
    else:
        print(payload, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
