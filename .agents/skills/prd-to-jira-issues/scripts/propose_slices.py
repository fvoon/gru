"""Propose a redline-ready slice plan for a validated parent PRD.

Two-phase script (the LLM in chat orchestrates):

  Phase 1 (no graphify fixture): emit a JSON action plan that tells the caller
  which graphify queries to run. Exits 0 with `status: "needs_graphify"`.

  Phase 2 (parent + graphify fixtures, plus an output redline path): synthesize
  the slice plan into a markdown file under `redlines/<parent-key>.md`. Prints a
  JSON summary (slice count, types, ordering edges) to stdout. Exits 0 on
  success, 2 on IO/parse errors, 3 if the redline file already exists and
  `--force` was not supplied (re-renders are destructive).

The output markdown is the human checkpoint between propose and write — PM/eng
edit it (drop slices, add ACs, reorder `depends_on:`, etc.) and the next script
(`write_jira_children.py`) consumes the redlined version.

Heuristics:
- Seed slices: one per canonical-repo bullet in the parent's `## Cross-Application Impact`.
- Splits: within a single repo, if the graphify modules partition into ≥2
  weakly-connected components, emit a separate slice for each component.
- Ordering: for any directed edge from a module in slice B's repo to a module
  in slice A's repo (different slices), record `depends_on: A` on slice B.
- Research/Design: lenient regex scan for `Spike:`, `Research:`, `Design:`,
  `TBD`, `unclear`, `needs investigation`. Each cluster becomes a Research
  (or Design) child; PM/eng can veto in the redline.
- Acceptance criteria: pre-fill the literal `TODO` placeholder so
  `write_jira_children.py`'s gate refuses to write until PM/eng fills them in.

Pure script: no MCP calls, no network, no clock unless `--generated-on` omitted.
"""

from __future__ import annotations

import argparse
import json
import re
import string
import sys
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

# Shared markdown helpers.
SCRIPT_DIR = Path(__file__).resolve().parent
_LIB_PATH = SCRIPT_DIR.parent.parent / "_lib"
if str(_LIB_PATH) not in sys.path:
    sys.path.insert(0, str(_LIB_PATH))

from markdown import split_top_level_sections  # noqa: E402

# Must stay in lockstep with .agents/jira-conventions.md and validate_parent.py.
CANONICAL_COMPONENTS = (
    "payment-platform",
    "walletapi",
    "infrastructure",
    "spring-boot-starters",
)

# Canonical-repo bullet under `## Cross-Application Impact`. The repo name is
# what comes before the first colon; the rest is the slice one-liner.
_CROSS_APP_BULLET_RE = re.compile(r"^[-*+]\s+([\w.\-/]+)\s*:\s*(.+?)\s*$", re.MULTILINE)

# Lenient research/design markers. The `re.IGNORECASE` is intentional — PM
# drafts vary in casing.
_RESEARCH_MARKER_RE = re.compile(
    r"(?im)^(?:[-*+]\s+)?(spike|research|tbd|design)\s*:\s*(.+?)\s*$"
)
_RESEARCH_INLINE_RE = re.compile(
    r"(?i)\b(unclear|needs investigation|to be (?:determined|decided))\b[^\n]*"
)


# ----- data shapes -----


class SliceSeed:
    """One proposed child slice. `letter` is assigned at render time."""

    __slots__ = (
        "letter",
        "type",
        "component",
        "title",
        "one_liner",
        "modules",
        "depends_on",
        "user_stories",
        "background",
        "out_of_scope",
        "research_source",
    )

    def __init__(
        self,
        *,
        type: str,
        component: str,
        title: str,
        one_liner: str,
        modules: list[str] | None = None,
        user_stories: list[int] | None = None,
        background: str = "",
        out_of_scope: list[str] | None = None,
        research_source: str | None = None,
    ):
        self.letter = ""
        self.type = type
        self.component = component
        self.title = title
        self.one_liner = one_liner
        self.modules = modules or []
        self.depends_on: list[str] = []
        self.user_stories = user_stories or []
        self.background = background
        self.out_of_scope = out_of_scope or []
        self.research_source = research_source


# ----- parsing helpers -----


def parse_cross_app_bullets(section_body: str) -> list[tuple[str, str]]:
    """Extract `(repo, description)` tuples from a `## Cross-Application Impact` body.

    Only canonical repos are returned; non-canonical entries are dropped silently
    (the parent already passed validate_parent.py, which enforces a canonical
    primary Component, but Cross-App Impact may name auxiliary repos that are
    not gru-managed and we must not seed slices for them).
    """
    out: list[tuple[str, str]] = []
    for m in _CROSS_APP_BULLET_RE.finditer(section_body):
        repo, desc = m.group(1).strip(), m.group(2).strip()
        if repo in CANONICAL_COMPONENTS:
            out.append((repo, desc))
    return out


def parse_user_story_indices(section_body: str) -> list[int]:
    """Return the numeric indices of `^N. ` items under `## User Stories`."""
    return [int(m.group(1)) for m in re.finditer(r"^(\d+)\.\s", section_body, re.MULTILINE)]


def detect_research_clusters(full_prd: str) -> list[tuple[str, str]]:
    """Return `(kind, source_text)` for each Research/Design marker found.

    `kind` is `"Research "` or `"Design "` (trailing space matches the literal
    Jira issue type). Inline markers (`unclear`, `needs investigation`) are
    classified as Research. We stop at 5 markers to avoid runaway proposals;
    PM/eng can add more in the redline.
    """
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for m in _RESEARCH_MARKER_RE.finditer(full_prd):
        keyword = m.group(1).lower()
        text = m.group(2).strip()
        # We render the redline using the no-trailing-space form (`Research`/
        # `Design`) because parse_keyvalue_block strips trailing spaces anyway;
        # write_jira_children re-applies the space when emitting to Atlassian.
        kind = "Design" if keyword == "design" else "Research"
        key = (kind, text.lower())
        if key in seen:
            continue
        seen.add(key)
        found.append((kind, text))
        if len(found) >= 5:
            return found

    for m in _RESEARCH_INLINE_RE.finditer(full_prd):
        text = m.group(0).strip()
        key = ("Research", text.lower())
        if key in seen:
            continue
        seen.add(key)
        found.append(("Research", text))
        if len(found) >= 5:
            break

    return found


# ----- graphify helpers -----


def _modules_in_repo(graphify: dict, repo: str) -> list[str]:
    return [m["name"] for m in graphify.get("modules", []) if m.get("repo") == repo]


def _module_to_repo(graphify: dict) -> dict[str, str]:
    return {m["name"]: m.get("repo", "") for m in graphify.get("modules", [])}


def weakly_connected_components(
    nodes: Iterable[str], edges: Iterable[tuple[str, str]]
) -> list[list[str]]:
    """Standard Union-Find over an undirected view of the edges. Stable ordering.

    Returns components ordered by their first-seen node. Within each component,
    nodes preserve their input order.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    nodes_list = list(nodes)
    for n in nodes_list:
        parent[n] = n
    node_set = set(nodes_list)
    for u, v in edges:
        if u in node_set and v in node_set:
            union(u, v)

    grouped: dict[str, list[str]] = defaultdict(list)
    for n in nodes_list:  # preserve order
        grouped[find(n)].append(n)
    return list(grouped.values())


# ----- slice synthesis -----


def _compose_background(parent: dict, repo: str) -> str:
    """Pull Problem Statement + this-repo bullet from parent for the slice background."""
    sections = parent.get("sections", {})
    problem = sections.get("Problem Statement", "").strip()
    cross_app = sections.get("Cross-Application Impact", "")
    repo_bullets = [
        m.group(0).strip()
        for m in _CROSS_APP_BULLET_RE.finditer(cross_app)
        if m.group(1).strip() == repo
    ]
    parts = [problem] if problem else []
    if repo_bullets:
        parts.append("**" + repo + " impact**:\n" + "\n".join(repo_bullets))
    return "\n\n".join(parts)


def _seed_repo_slices(
    parent: dict, graphify: dict, repo: str, one_liner: str, all_user_stories: list[int]
) -> list[SliceSeed]:
    """Seed one or more slices for `repo`, splitting on weakly-connected components."""
    repo_modules = _modules_in_repo(graphify, repo)
    edges = [
        (e["from"], e["to"])
        for e in graphify.get("edges", [])
        if e.get("from") in repo_modules and e.get("to") in repo_modules
    ]
    components = weakly_connected_components(repo_modules, edges)

    # Drop empty components (can happen if graphify returned no modules for the repo
    # — fall back to a single slice carrying the full one-liner).
    components = [c for c in components if c] or [[]]

    background = _compose_background(parent, repo)

    if len(components) == 1:
        modules = components[0]
        return [
            SliceSeed(
                type="Task",
                component=repo,
                title=f"[BE] {one_liner}",
                one_liner=one_liner,
                modules=modules,
                user_stories=all_user_stories,
                background=background,
            )
        ]

    out: list[SliceSeed] = []
    for idx, comp in enumerate(components):
        suffix = f" (part {idx + 1} of {len(components)})"
        primary_module = comp[0] if comp else ""
        title = f"[BE] {one_liner}{suffix}"
        if primary_module:
            title = f"[BE] {primary_module}: {one_liner}{suffix}"
        out.append(
            SliceSeed(
                type="Task",
                component=repo,
                title=title,
                one_liner=f"{one_liner}{suffix}",
                modules=comp,
                user_stories=all_user_stories,
                background=background,
                out_of_scope=[
                    f"Modules in part {j + 1} of {len(components)} (handled by sibling slice)"
                    for j in range(len(components))
                    if j != idx
                ],
            )
        )
    return out


def _add_research_slices(
    parent: dict, research_clusters: list[tuple[str, str]]
) -> list[SliceSeed]:
    """One Research/Design slice per detected cluster, all bound to the parent's primary repo."""
    primary = parent.get("primary_component", "")
    out: list[SliceSeed] = []
    background = _compose_background(parent, primary)

    for kind, text in research_clusters:
        prefix = "Design:" if kind == "Design" else "Spike:"
        title = f"{prefix} {text}"
        out.append(
            SliceSeed(
                type=kind,
                component=primary,
                title=title,
                one_liner=text,
                user_stories=[],  # Research/Design rarely covers user stories directly
                background=background,
                research_source=text,
            )
        )
    return out


def _assign_letters(slices: list[SliceSeed]) -> None:
    if len(slices) > len(string.ascii_uppercase):
        raise ValueError(
            f"too many proposed slices ({len(slices)}); maximum is "
            f"{len(string.ascii_uppercase)}. Narrow the parent PRD scope."
        )
    for i, s in enumerate(slices):
        s.letter = string.ascii_uppercase[i]


def _propose_ordering(slices: list[SliceSeed], graphify: dict) -> None:
    """Populate `depends_on` based on directed cross-slice edges in graphify.

    For each edge `from -> to`, if `from` lives in slice X and `to` lives in slice
    Y (different slices), then X depends on Y (X uses something Y provides).
    """
    module_to_slice: dict[str, str] = {}
    for s in slices:
        for mod in s.modules:
            module_to_slice[mod] = s.letter

    seen_edges: set[tuple[str, str]] = set()
    for e in graphify.get("edges", []):
        src, dst = e.get("from"), e.get("to")
        if not src or not dst:
            continue
        x, y = module_to_slice.get(src), module_to_slice.get(dst)
        if not x or not y or x == y:
            continue
        if (x, y) in seen_edges:
            continue
        seen_edges.add((x, y))

    by_letter = {s.letter: s for s in slices}
    for x, y in sorted(seen_edges):
        if y not in by_letter[x].depends_on:
            by_letter[x].depends_on.append(y)


# ----- markdown rendering -----


def _render_acceptance_criteria_stub(stories: list[int]) -> str:
    """Always include the literal `TODO` so write_jira_children's gate trips."""
    lines = [
        "<!-- TODO: PM/eng must fill in. write_jira_children.py refuses to write "
        "if any AC contains the literal `TODO`. -->"
    ]
    if stories:
        for s in stories[:3]:
            lines.append(f"- [ ] (testable criterion derived from user story {s})")
    else:
        lines.append("- [ ] (testable criterion 1)")
        lines.append("- [ ] (testable criterion 2)")
    return "\n".join(lines)


def _render_slice(s: SliceSeed, parent_key: str) -> str:
    """One `## Slice X — ...` section. Trailing newline omitted (writer concatenates)."""
    depends_on = ", ".join(s.depends_on) if s.depends_on else "(none)"
    modules = ", ".join(s.modules) if s.modules else "(unknown — fill from graphify)"
    user_stories = (
        ", ".join(str(i) for i in s.user_stories) if s.user_stories else "(none)"
    )

    if s.research_source:
        what_to_build = (
            f"## Investigation\n\n"
            f"{s.research_source}\n\n"
            "Document findings in a comment on this issue and link any follow-up tickets."
        )
    else:
        what_to_build = (
            f"## What to build (in {s.component})\n\n{s.one_liner}"
        )

    out_of_scope_block = ""
    if s.out_of_scope:
        bullets = "\n".join(f"- {b}" for b in s.out_of_scope)
        out_of_scope_block = f"\n\n## Out of Scope (this slice)\n\n{bullets}"

    blocked_by_block = (
        "## Blocked by\n\n- "
        + (
            "\n- ".join(f"Slice {letter}" for letter in s.depends_on)
            if s.depends_on
            else "None — can start immediately"
        )
    )

    user_story_block = (
        "## User stories addressed\n\n"
        + (
            "\n".join(
                f"- User story {i} (from parent {parent_key})" for i in s.user_stories
            )
            if s.user_stories
            else f"- (none — derived from parent {parent_key})"
        )
    )

    background = s.background or (
        f"(parent had no Problem Statement; consult parent {parent_key})"
    )
    description = (
        f"{what_to_build}\n\n"
        "## Background (auto-snapshot from parent — do not edit)\n\n"
        f"{background}\n\n"
        "## Acceptance criteria\n\n"
        f"{_render_acceptance_criteria_stub(s.user_stories)}\n\n"
        "## Implementation Hints (graphify-derived)\n\n"
        f"- Modules to touch: {modules}"
        f"{out_of_scope_block}\n\n"
        f"{blocked_by_block}\n\n"
        f"{user_story_block}"
    )

    indented_description = "\n".join("  " + line for line in description.splitlines())

    header_lines = [
        f"## Slice {s.letter} — {s.component}: {s.one_liner}",
        f"- type: {s.type}",
        f"- title: {s.title}",
        f"- component: {s.component}",
        f"- depends_on: {depends_on}",
        f"- modules: {modules}",
        f"- user_stories: {user_stories}",
        "- description: |",
    ]

    return "\n".join(header_lines) + "\n" + indented_description


def render_slice_plan(
    parent: dict,
    slices: list[SliceSeed],
    *,
    generated_on: str,
) -> str:
    """Top-level renderer. Pure function; output is byte-stable for fixed inputs."""
    parent_key = parent.get("key", "<unknown>")
    summary = parent.get("summary", "")
    title = f"# Slice plan for {parent_key}: {summary}".rstrip()

    preamble = (
        f"> Generated by propose_slices.py from {parent_key} + graphify on {generated_on}.\n"
        "> Edit freely: delete a `## Slice` section to drop, reorder slices to reorder, "
        "edit `depends_on:` to change blocking, edit prose. write_jira_children.py "
        "parses this file back into actions."
    )

    body_parts = [_render_slice(s, parent_key) for s in slices]
    return title + "\n\n" + preamble + "\n\n" + "\n\n".join(body_parts) + "\n"


# ----- top-level orchestration -----


def emit_phase1_plan(parent: dict) -> dict:
    """Phase 1: tell the LLM which graphify queries to run."""
    impl_decisions = parent.get("sections", {}).get("Implementation Decisions", "")
    return {
        "status": "needs_graphify",
        "actions": [
            {
                "step": 1,
                "tool": "graphify.search",
                "args": {"text": impl_decisions[:2000]},
                "purpose": (
                    "Search the codebase graph for the modules named in the parent's "
                    "## Implementation Decisions section."
                ),
            },
            {
                "step": 2,
                "tool": "graphify.get_node",
                "args": {"id": "<PER_HIT>"},
                "purpose": "Fetch metadata for each search hit.",
            },
            {
                "step": 3,
                "tool": "graphify.get_edges",
                "args": {"from_id": "<EACH_HIT>"},
                "purpose": (
                    "Fetch outbound edges per hit so propose_slices.py can compute "
                    "weakly-connected components and cross-slice ordering."
                ),
            },
        ],
        "next_step": (
            "Aggregate the graphify responses into a JSON object with keys "
            "{modules: [{name, repo}], edges: [{from, to, type}]} and re-invoke "
            "propose_slices.py with --graphify-fixture <path>."
        ),
    }


def synthesize(parent: dict, graphify: dict) -> list[SliceSeed]:
    """Build the full slice list (cross-app slices + research/design)."""
    sections = parent.get("sections", {})
    cross_app = sections.get("Cross-Application Impact", "")
    user_stories = sections.get("User Stories", "")
    full_prd = parent.get("full_prd_text", "")

    bullets = parse_cross_app_bullets(cross_app)
    story_indices = parse_user_story_indices(user_stories)

    slices: list[SliceSeed] = []
    for repo, one_liner in bullets:
        slices.extend(_seed_repo_slices(parent, graphify, repo, one_liner, story_indices))

    slices.extend(_add_research_slices(parent, detect_research_clusters(full_prd)))

    if not slices:
        # Fallback: at least one slice for the primary component so the file is non-empty.
        primary = parent.get("primary_component", "")
        if primary:
            slices.append(
                SliceSeed(
                    type="Task",
                    component=primary,
                    title=f"[BE] {parent.get('summary', '')}".strip(),
                    one_liner=parent.get("summary", ""),
                    user_stories=story_indices,
                    background=_compose_background(parent, primary),
                )
            )

    _assign_letters(slices)
    _propose_ordering(slices, graphify)
    return slices


def summary_json(slices: list[SliceSeed]) -> dict:
    types: dict[str, int] = defaultdict(int)
    for s in slices:
        types[s.type] += 1
    edges = [(s.letter, dep) for s in slices for dep in s.depends_on]
    return {
        "status": "ok",
        "slice_count": len(slices),
        "types": dict(types),
        "ordering_edges": [{"from": a, "to": b} for a, b in edges],
        "letters": [s.letter for s in slices],
    }


# ----- CLI -----


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--parent-fixture",
        type=Path,
        required=True,
        help="Path to the validated parent JSON (output of validate_parent.py).",
    )
    parser.add_argument(
        "--graphify-fixture",
        type=Path,
        default=None,
        help="Path to a graphify fixture JSON. If absent, emits the phase-1 query plan.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Output redline path. Defaults to "
            "../redlines/<parent-key>.md relative to the script."
        ),
    )
    parser.add_argument(
        "--generated-on",
        default=None,
        help=(
            "Override the timestamp shown in the file preamble. Defaults to the "
            "current UTC time at minute precision; tests pin this for byte-stable "
            "output."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing redline file. Default: refuse.",
    )
    args = parser.parse_args(argv)

    try:
        parent = json.loads(args.parent_fixture.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(
            json.dumps({"status": "error", "reason": f"parent fixture is not valid JSON: {exc}"}),
            file=sys.stderr,
        )
        return 2

    if not isinstance(parent, dict) or "key" not in parent:
        print(
            json.dumps(
                {
                    "status": "error",
                    "reason": "parent fixture must be a normalized parent JSON object",
                }
            ),
            file=sys.stderr,
        )
        return 2

    # Re-derive sections if validate_parent didn't include them (defensive: tests
    # may pass a minimal parent fixture).
    if "sections" not in parent and parent.get("full_prd_text"):
        parent["sections"] = split_top_level_sections(parent["full_prd_text"])

    if args.graphify_fixture is None:
        print(json.dumps(emit_phase1_plan(parent), indent=2))
        return 0

    try:
        graphify = json.loads(args.graphify_fixture.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(
            json.dumps({"status": "error", "reason": f"graphify fixture is not valid JSON: {exc}"}),
            file=sys.stderr,
        )
        return 2

    if not isinstance(graphify, dict):
        print(
            json.dumps({"status": "error", "reason": "graphify fixture must be a JSON object"}),
            file=sys.stderr,
        )
        return 2

    out_path = args.out or (SCRIPT_DIR.parent / "redlines" / f"{parent['key']}.md")

    if out_path.exists() and not args.force:
        print(
            json.dumps(
                {
                    "status": "exists",
                    "reason": (
                        f"redline file already exists at {out_path}; pass --force to "
                        "overwrite. Re-rendering is destructive — your edits will be lost."
                    ),
                    "path": str(out_path),
                }
            ),
            file=sys.stderr,
        )
        return 3

    try:
        slices = synthesize(parent, graphify)
    except ValueError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2

    generated_on = args.generated_on or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%MZ"
    )
    rendered = render_slice_plan(parent, slices, generated_on=generated_on)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")

    summary = summary_json(slices)
    summary["path"] = str(out_path)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
