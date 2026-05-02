"""Convert a redlined spike report into a deterministic Atlassian write plan.

Inputs:
- `--report <path>` — the redlined `redlines/spike-<KEY>-report.md` (the
  engineer has filled in the `TODO` placeholders).
- `--state-fixture <path>` — `.spike-state.json` written by bootstrap_worktree.py
  (provides Confluence parent id, started_at, repos, etc.). Defaults to looking
  up `<worktree-base>/spike/<KEY>/.spike-state.json` based on the report path.
- Optional `--graphify-summary <path>` — Confluence-storage-formatted graphify
  block for section 2 ("Existing state (from graphify)"). Defaults to
  re-rendering from the state file's research_summary if absent. (In normal
  use the agent passes the same graphify summary the briefing used; tests
  may stub.)

Output: a 3-phase JSON action plan on stdout for the LLM to execute via the
Atlassian MCP:

  Phase 1: createConfluencePage  (or updateConfluencePage with --update-existing)
  Phase 2: addCommentToJiraIssue (with the resolved Confluence URL)
  Phase 3: transitionJiraIssue   (Research ticket -> Done)

Validation gates (all must pass):
- All 7 required sections (`## Question`, `## Existing state (from graphify)`,
  `## What was tried`, `## What worked / what didn't`, `## Recommended approach`,
  `## Suggested follow-up tickets`, `## Spike metadata`) are present.
- Sections 1, 3, 4, 5, 6 contain no literal `TODO` markers.
- `.spike-state.json` is loadable and carries `confluence_parent_page_id` +
  `research_key` + `started_at`.

The script never calls Atlassian; the LLM is the executor. It also never opens
files or shells out beyond reading the report + state-file inputs.

Markdown -> Confluence storage-format conversion: minimal, in-script. Supports
the 7-section template only — headings (h2/h3), paragraphs, bullet lists,
fenced code blocks, inline `code`, and `[text](url)` links. Anything else
passes through escaped (defensive). The agent must not paste Confluence
panels / expand macros / etc. into the redline; those would render as escaped
text rather than rich Confluence components.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Shared markdown helpers live in `.agents/skills/_lib/`.
SCRIPT_DIR = Path(__file__).resolve().parent
_LIB_PATH = SCRIPT_DIR.parent.parent / "_lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

from markdown import split_top_level_sections  # noqa: E402

# ----- shared constants -----


REQUIRED_SECTIONS = (
    "Question",
    "Existing state (from graphify)",
    "What was tried",
    "What worked / what didn't",
    "Recommended approach",
    "Suggested follow-up tickets",
    "Spike metadata",
)

# Sections that the engineer / agent fills in; must be `TODO`-free at publish.
# Sections 2 ("Existing state ...") and 7 ("Spike metadata") are
# auto-populated and skipped by the TODO check.
TODO_GUARDED_SECTIONS = (
    "Question",
    "What was tried",
    "What worked / what didn't",
    "Recommended approach",
    "Suggested follow-up tickets",
)


WALL_CLOCK_BUDGET_MINUTES = 30  # mirror of bootstrap_worktree's constant.


# ----- exceptions -----


class InvalidReport(Exception):
    """Report markdown fails a validation gate (exit 1)."""


class StateFileError(Exception):
    """`.spike-state.json` missing keys / malformed (exit 2)."""


# ----- report parsing + validation -----


def parse_report(text: str) -> dict[str, str]:
    """Split the report into top-level sections and return `{name: body}`.

    Raises `InvalidReport` if any required section is missing.
    """
    sections = split_top_level_sections(text)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        raise InvalidReport(
            "report missing required section(s): "
            + ", ".join(f"## {s}" for s in missing)
        )
    return {s: sections[s] for s in REQUIRED_SECTIONS}


_TODO_TOKEN_RE = re.compile(r"\bTODO\b")


def find_todo_violations(parsed: dict[str, str]) -> list[str]:
    """Return the list of TODO-guarded section names that still contain `TODO`."""
    out: list[str] = []
    for section in TODO_GUARDED_SECTIONS:
        body = parsed.get(section, "")
        if _TODO_TOKEN_RE.search(body):
            out.append(section)
    return out


# ----- state file -----


_REQUIRED_STATE_KEYS = (
    "research_key",
    "started_at",
    "confluence_parent_page_id",
    "spikes_parent_page_id",
    "repos",
)


def load_state(path: Path) -> dict:
    """Read + sanity-check `.spike-state.json`. Raises `StateFileError`."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        raise StateFileError(f"failed to read {path}: {exc}") from exc
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateFileError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise StateFileError(f"{path} must be a JSON object")
    missing = [k for k in _REQUIRED_STATE_KEYS if k not in state]
    if missing:
        raise StateFileError(f"{path} missing keys: {missing}")
    return state


def compute_elapsed_minutes(started_at: str, now: datetime | None = None) -> int:
    """Return integer wall-clock minutes elapsed since `started_at`.

    `started_at` is expected to be ISO-8601 with timezone (e.g.
    `2026-05-02T18:00:00+00:00`). Floors to integer minutes; on parse failure,
    returns -1 so the report footer can show `unknown` rather than crash.
    """
    try:
        start = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return -1
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = now or datetime.now(timezone.utc)
    delta = end - start
    return max(0, int(delta.total_seconds() // 60))


# ----- markdown -> Confluence storage format -----


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)


def _xml_escape(s: str) -> str:
    """Conservative XML-escape; covers the entities Confluence storage cares about."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_inline(text: str) -> str:
    """Render inline markdown (links + inline code) to Confluence storage format.

    Order matters: extract code spans first (they protect their content from
    further processing), then links, then escape the rest.
    """
    placeholders: list[tuple[str, str]] = []

    def stash(replacement: str) -> str:
        token = f"\x00\x00PH{len(placeholders)}\x00\x00"
        placeholders.append((token, replacement))
        return token

    def code_sub(m: re.Match) -> str:
        return stash(f"<code>{_xml_escape(m.group(1))}</code>")

    def link_sub(m: re.Match) -> str:
        label = _xml_escape(m.group(1))
        href = _xml_escape(m.group(2))
        return stash(f'<a href="{href}">{label}</a>')

    text = _INLINE_CODE_RE.sub(code_sub, text)
    text = _LINK_RE.sub(link_sub, text)
    text = _xml_escape(text)
    for token, replacement in placeholders:
        text = text.replace(token, replacement)
    return text


def _render_fence_block(language: str, code: str) -> str:
    """Render a fenced code block as a Confluence `code` macro."""
    lang_param = ""
    if language:
        lang_param = (
            '<ac:parameter ac:name="language">'
            + _xml_escape(language.strip())
            + "</ac:parameter>"
        )
    return (
        '<ac:structured-macro ac:name="code">'
        + lang_param
        + "<ac:plain-text-body><![CDATA["
        + code.rstrip("\n")
        + "]]></ac:plain-text-body>"
        + "</ac:structured-macro>"
    )


def markdown_to_confluence_storage(md: str) -> str:
    """Convert markdown body to Confluence storage XHTML.

    Supports the 7-section template's needs:
    - `## ` -> `<h2>`, `### ` -> `<h3>`
    - Bullet lines (`- ` / `* ` / `+ `) -> `<ul><li>`
    - Fenced code blocks -> `<ac:structured-macro ac:name="code">`
    - Inline `code` -> `<code>`
    - `[label](url)` -> `<a>`
    - Paragraphs -> `<p>`

    Anything that doesn't match these patterns is wrapped in a paragraph and
    XML-escaped. We treat the input as already-flattened Confluence-shaped
    markdown — this is the redline file the engineer authored, not arbitrary
    user input — so a defensive escape-and-pass is fine.
    """
    out_parts: list[str] = []
    # First pass: pluck fenced code blocks out of `md` so their content isn't
    # processed by line-level rules. Replace each with a placeholder.
    fences: list[str] = []

    def fence_sub(m: re.Match) -> str:
        language = m.group(1) or ""
        body = m.group(2)
        fences.append(_render_fence_block(language, body))
        return f"\x00\x00FENCE{len(fences) - 1}\x00\x00"

    md = _FENCE_RE.sub(fence_sub, md)

    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Fence placeholder — emit as-is.
        if line.startswith("\x00\x00FENCE"):
            idx = int(line[len("\x00\x00FENCE"):].rstrip("\x00"))
            out_parts.append(fences[idx])
            i += 1
            continue

        # Headings.
        if line.startswith("### "):
            out_parts.append(f"<h3>{_render_inline(line[4:].strip())}</h3>")
            i += 1
            continue
        if line.startswith("## "):
            out_parts.append(f"<h2>{_render_inline(line[3:].strip())}</h2>")
            i += 1
            continue

        # Block quote (used by `> Status: DRAFT` and `> Generated by ...`).
        if line.startswith("> "):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].startswith("> "):
                quote_lines.append(lines[i][2:])
                i += 1
            out_parts.append(
                "<blockquote>"
                + _render_inline(" ".join(quote_lines))
                + "</blockquote>"
            )
            continue

        # Bullet list.
        if re.match(r"^[-*+]\s+", line):
            list_items: list[str] = []
            while i < len(lines) and re.match(r"^[-*+]\s+", lines[i]):
                content = re.sub(r"^[-*+]\s+", "", lines[i])
                list_items.append(f"<li>{_render_inline(content)}</li>")
                i += 1
            out_parts.append("<ul>" + "".join(list_items) + "</ul>")
            continue

        # Blank line — separator between paragraphs.
        if not line.strip():
            i += 1
            continue

        # Paragraph: gather consecutive non-blank lines until blank / heading /
        # list / fence placeholder.
        para_lines: list[str] = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if (
                not nxt.strip()
                or nxt.startswith(("#", "> ", "\x00\x00FENCE"))
                or re.match(r"^[-*+]\s+", nxt)
            ):
                break
            para_lines.append(nxt)
            i += 1
        out_parts.append("<p>" + _render_inline(" ".join(para_lines)) + "</p>")

    return "\n".join(out_parts)


# ----- report assembly -----


def render_metadata_section(
    state: dict, elapsed_minutes: int, repos: list[str], branch: str
) -> str:
    """Build the auto-populated `## Spike metadata` body."""
    if elapsed_minutes < 0:
        elapsed_str = "unknown"
        verdict = "unknown (could not parse `started_at`)"
    elif elapsed_minutes <= WALL_CLOCK_BUDGET_MINUTES:
        elapsed_str = f"{elapsed_minutes} min"
        verdict = f"within budget ({elapsed_minutes} / {WALL_CLOCK_BUDGET_MINUTES} min)"
    else:
        elapsed_str = f"{elapsed_minutes} min"
        verdict = (
            f"BUDGET_EXCEEDED ({elapsed_minutes} / {WALL_CLOCK_BUDGET_MINUTES} min) "
            "— consider scoping the next spike tighter"
        )
    repos_fmt = ", ".join(f"`{r}`" for r in repos)
    parent_key = state.get("parent_key")
    parent_line = (
        f"- **Parent ticket:** `{parent_key}`"
        if parent_key
        else "- **Parent ticket:** _none (orphan spike)_"
    )
    return (
        f"- **Research ticket:** `{state['research_key']}`\n"
        f"{parent_line}\n"
        f"- **Repos:** {repos_fmt}\n"
        f"- **Worktree branch:** `{branch}`\n"
        f"- **Started at:** {state['started_at']}\n"
        f"- **Elapsed:** {elapsed_str}\n"
        f"- **Budget verdict:** {verdict}\n"
    )


def assemble_publishable_report(
    parsed: dict[str, str],
    state: dict,
    graphify_summary: str | None,
    elapsed_minutes: int,
) -> str:
    """Compose the final markdown the script will convert + publish.

    Replaces the `_(auto-populated ...)_` placeholders for sections 2 and 7
    with their final content; leaves agent-fill sections untouched.
    """
    research_key = state["research_key"]
    branch = f"spike/{research_key}"
    repos = state.get("repos", [])

    final_existing_state = (
        graphify_summary
        if graphify_summary
        else parsed["Existing state (from graphify)"]
    )
    final_metadata = render_metadata_section(state, elapsed_minutes, repos, branch)

    # Section name with an apostrophe — extracted so f-string expressions don't
    # need escaped quotes (which the older parser rejects).
    worked_didnt_section = "What worked / what didn't"
    worked_didnt_body = parsed[worked_didnt_section].strip()

    out_parts = [
        f"# Spike report — {research_key}\n",
        f"## Question\n\n{parsed['Question'].strip()}\n",
        f"## Existing state (from graphify)\n\n{final_existing_state.strip()}\n",
        f"## What was tried\n\n{parsed['What was tried'].strip()}\n",
        f"## {worked_didnt_section}\n\n{worked_didnt_body}\n",
        f"## Recommended approach\n\n{parsed['Recommended approach'].strip()}\n",
        f"## Suggested follow-up tickets\n\n{parsed['Suggested follow-up tickets'].strip()}\n",
        f"## Spike metadata\n\n{final_metadata.strip()}\n",
    ]
    return "\n".join(out_parts)


# ----- action plan -----


def page_title(state: dict, parsed: dict[str, str]) -> str:
    """Confluence page title = `Spike: <Question summary line>`.

    Pulls the first line of the Question body as the spike title. The
    skeleton convention is that the first line is `**Spike: <title>?**`,
    so we strip a leading `Spike:` (case-insensitive) to avoid `Spike: Spike: ...`.
    Falls back to `Spike: <research_key>` if the Question body is empty.
    """
    body = parsed.get("Question", "").strip()
    if body:
        first_line = body.split("\n", 1)[0].strip("*_ ").rstrip(".")
        # Strip a leading `Spike:` (or `spike:`) so we don't double-prefix.
        cleaned = re.sub(r"^[Ss]pike:\s*", "", first_line)
        return f"Spike: {cleaned}"
    return f"Spike: {state.get('research_key', 'untitled')}"


def build_action_plan(
    state: dict,
    parsed: dict[str, str],
    graphify_summary: str | None,
    elapsed_minutes: int,
    update_existing: bool,
    existing_page_id: str | None,
) -> dict:
    """3-phase action plan for the LLM."""
    research_key = state["research_key"]
    title = page_title(state, parsed)
    publishable_md = assemble_publishable_report(
        parsed, state, graphify_summary, elapsed_minutes
    )
    storage_format = markdown_to_confluence_storage(publishable_md)

    actions: list[dict] = []

    if update_existing:
        if not existing_page_id:
            raise InvalidReport(
                "--update-existing requires an --existing-page-id (the Confluence "
                "page id of the prior spike report on this Research ticket)"
            )
        actions.append(
            {
                "step": 1,
                "phase": "publish",
                "tool": "atlassian.updateConfluencePage",
                "args": {
                    "pageId": existing_page_id,
                    "title": title,
                    "body": {"storage": {"value": storage_format, "representation": "storage"}},
                },
                "purpose": "Overwrite the existing spike report Confluence page.",
            }
        )
    else:
        actions.append(
            {
                "step": 1,
                "phase": "publish",
                "tool": "atlassian.createConfluencePage",
                "args": {
                    "spaceId": state.get("confluence_space_id"),
                    "parentPageId": state["confluence_parent_page_id"],
                    "title": title,
                    "body": {"storage": {"value": storage_format, "representation": "storage"}},
                },
                "purpose": (
                    "Create the spike report as a sub-page under the resolved "
                    "Confluence parent (elevated PRD page or Spikes folder)."
                ),
            }
        )

    actions.append(
        {
            "step": 2,
            "phase": "comment",
            "tool": "atlassian.addCommentToJiraIssue",
            "args": {
                "issueKey": research_key,
                "body": (
                    "Spike report published: {{confluence_page_url}}. "
                    f"Worktree branch `spike/{research_key}` left in place; "
                    "see SPIKE_README.md for cleanup commands."
                ),
            },
            "purpose": (
                "Post the Confluence page URL on the Research ticket so the "
                "audit trail lives in Jira too."
            ),
        }
    )

    actions.append(
        {
            "step": 3,
            "phase": "transition",
            "tool": "atlassian.transitionJiraIssue",
            "args": {
                "issueKey": research_key,
                # Done is not directly reachable from To Do — the agent
                # must call atlassian.getJiraIssueTransitions first to find
                # the right id (workflow can vary by issue type). The script
                # emits the human-readable target name; the agent resolves
                # the id at execute time.
                "transition": {"name": "Done"},
            },
            "purpose": (
                "Transition the Research ticket to Done. The agent must call "
                "atlassian.getJiraIssueTransitions(issueKey) first to find "
                "the actual transition id (workflow varies); this action "
                "carries only the target status name."
            ),
        }
    )

    return {
        "status": "ok",
        "research_key": research_key,
        "page_title": title,
        "elapsed_minutes": elapsed_minutes,
        "budget_minutes": WALL_CLOCK_BUDGET_MINUTES,
        "update_existing": update_existing,
        "actions": actions,
    }


# ----- driver -----


def run(args: argparse.Namespace) -> tuple[int, dict | str]:
    # 1. Read report.
    try:
        report_text = args.report.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        return (2, {"status": "error", "reason": f"failed to read report: {exc}"})

    # 2. Parse + validate sections.
    try:
        parsed = parse_report(report_text)
    except InvalidReport as exc:
        return (1, {"status": "invalid_report", "reason": str(exc)})

    todo_offenders = find_todo_violations(parsed)
    if todo_offenders:
        return (
            1,
            {
                "status": "todo_remaining",
                "reason": (
                    "the following section(s) still contain literal `TODO` "
                    "placeholders; engineer must fill them in before publish: "
                    + ", ".join(todo_offenders)
                ),
                "sections": todo_offenders,
            },
        )

    # 3. Read state file.
    try:
        state = load_state(args.state_fixture)
    except StateFileError as exc:
        return (2, {"status": "state_error", "reason": str(exc)})

    # 4. Compute elapsed minutes.
    fixed_now: datetime | None = None
    if args.now is not None:
        try:
            fixed_now = datetime.fromisoformat(args.now)
            if fixed_now.tzinfo is None:
                fixed_now = fixed_now.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            return (2, {"status": "error", "reason": f"--now is not ISO-8601: {exc}"})
    elapsed_minutes = compute_elapsed_minutes(state["started_at"], fixed_now)

    # 5. Optionally read graphify summary override.
    graphify_summary: str | None = None
    if args.graphify_summary is not None:
        try:
            graphify_summary = args.graphify_summary.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            return (2, {"status": "error", "reason": f"failed to read graphify summary: {exc}"})

    # 6. Build the plan.
    try:
        plan = build_action_plan(
            state=state,
            parsed=parsed,
            graphify_summary=graphify_summary,
            elapsed_minutes=elapsed_minutes,
            update_existing=args.update_existing,
            existing_page_id=args.existing_page_id,
        )
    except InvalidReport as exc:
        return (1, {"status": "invalid_report", "reason": str(exc)})

    return (0, plan)


# ----- CLI -----


def _default_state_fixture_for(report: Path) -> Path:
    """Default `--state-fixture` lookup: `<worktree-base>/spike/<KEY>/.spike-state.json`.

    Heuristic: the report file is `redlines/spike-<KEY>-report.md`. Extract
    KEY, then assume `<worktree-base>` is `~/IdeaProjects` (matching
    bootstrap_worktree.py's default).
    """
    name = report.name
    m = re.match(r"^spike-(?P<key>[A-Z]+-\d+)-report\.md$", name)
    if not m:
        return Path("/dev/null/.spike-state.json")
    key = m.group("key")
    return Path.home() / "IdeaProjects" / "spike" / key / ".spike-state.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="Path to the redlined `redlines/spike-<KEY>-report.md`.",
    )
    parser.add_argument(
        "--state-fixture",
        type=Path,
        default=None,
        help=(
            "Path to `.spike-state.json` (defaults to "
            "<worktree-base>/spike/<KEY>/.spike-state.json based on the report filename)."
        ),
    )
    parser.add_argument(
        "--graphify-summary",
        type=Path,
        default=None,
        help=(
            "Optional path to a markdown file containing the rendered "
            "graphify-summary block to drop into section 2. If absent, the "
            "current contents of section 2 in the report are kept."
        ),
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help=(
            "Overwrite an existing Confluence page instead of creating a new "
            "one. Requires --existing-page-id."
        ),
    )
    parser.add_argument(
        "--existing-page-id",
        type=str,
        default=None,
        help="Confluence page id to overwrite (used with --update-existing).",
    )
    parser.add_argument(
        "--now",
        type=str,
        default=None,
        help=(
            "ISO-8601 UTC timestamp to use as 'now' for elapsed-minutes "
            "computation. Defaults to the current UTC time. Tests inject a "
            "fixed value for deterministic footer output."
        ),
    )
    args = parser.parse_args(argv)

    if args.state_fixture is None:
        args.state_fixture = _default_state_fixture_for(args.report)

    code, payload = run(args)
    if isinstance(payload, dict):
        target = sys.stderr if code != 0 else sys.stdout
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=target)
    else:
        print(payload, file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
