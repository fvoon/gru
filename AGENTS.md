# gru — Agent Instructions

## Context

gru is an **LLM-agent harness** for PLTPM Jira/Confluence work. The five layers (role, knowledge, tools, deterministic gates, conventions) are documented in the [README's Architecture section](README.md#architecture). When working IN gru, you're extending the harness — not building a feature on top of it. Skills are the role layer; Python scripts are the gate layer; action-plan JSON is the handoff between them.

## Engineering preferences

- DRY is important — flag repetition aggressively.
- Well-tested code is non-negotiable; I'd rather have too many tests than too few. (For gru itself this mostly applies to scripts and any code-generating skill helpers; pure-prose skill bodies are reviewed for clarity instead.)
- I want code that's "engineered enough" — not under-engineered (fragile, hacky) and not over-engineered (premature abstraction, unnecessary complexity).
- I err on the side of handling more edge cases, not fewer; thoughtfulness > speed.
- Bias toward explicit over clever.

## Plan-mode review process

Review plans thoroughly before making any code changes. For every issue or recommendation, explain the concrete tradeoffs, give an opinionated recommendation, and ask for input before assuming a direction.

### For each issue found

For every specific issue (bug, smell, design concern, or risk):
- Describe the problem concretely, with file and line references.
- Present 2–3 options, including "do nothing" where reasonable.
- For each option, specify: implementation effort, risk, impact on other code, and maintenance burden.
- Give a recommended option, and why, mapped to my preferences above.
- Then explicitly ask whether I agree or want to choose a different direction before proceeding.

### Workflow and interaction

- Do not assume my priorities on timeline or scale.
- After each review section, pause and ask for my feedback before moving on.
- NUMBER issues and give LETTERS for options so choices are unambiguous.
- Make the recommended option always the 1st option presented.

## gru-specific rules

### Source of truth

- [`docs/plan.md`](docs/plan.md) is authoritative for *what* gru does and *why*. Update the plan when scope, design, conventions, or skill contracts change. Code/skill drift from the plan is a bug.
- [`.agents/jira-conventions.md`](.agents/jira-conventions.md) is authoritative for Jira workflow status names, Component-to-repo mapping, parent/child issue type heuristics, link types, and ceremony sub-task names. Skills MUST read these from the file at runtime — never hardcode them in skill bodies.

### Skill authoring

All skills under `.agents/skills/` follow the AI Hero [write-a-skill](https://github.com/ai-hero-dev/cohort-003-project/blob/live-run-through-modified/.claude/skills/write-a-skill/SKILL.md) conventions. In short:

- Frontmatter: `name`, `description` (≤ 1024 chars; description must communicate when to trigger).
- `SKILL.md` body ≤ 100 lines. Move detail into sibling files: `REFERENCE.md`, `EXAMPLES.md`, `scripts/`.
- "Quick start" → "Workflows" → "Advanced features" structure.
- Skills must be invocable verbatim by both Cursor agent and Claude Code (consumed via the `.cursor/skills` and `.claude/skills` symlinks).

### Narrative layer authoring

- `knowledge/narrative/` is the human-curated complement to graphify. If graphify already says it, don't repeat it here. The narrative layer captures **why** and **what's missing from the structural graph**: cross-app event chains, semantic mismatches, owner/convention tribal knowledge.
- Each `flows/<flow>.md` file: a single mermaid sequence diagram + 1–3 paragraphs of when/why context. No code dumps.
- `glossary.md`: bullet entries, each one term-mismatch per row (e.g., "`Transfer` in payment-platform vs. `Transaction` in walletapi"), with the bridging mental model.
- `repos/<name>.md`: a short README-style overview — purpose, owners, dominant conventions, what graphify systematically misses (e.g., Axon event wiring).

### When making changes

- Keep `docs/plan.md` in sync. If a code/skill change implies a plan change, update the plan in the same commit.
- Symlinks `.cursor/skills` and `.claude/skills` are authoritative pointers — never duplicate skill content into either directory; always edit the canonical `.agents/skills/<name>/`.
- Treat `~/payments-graph/` and the four product repos as **read-only** from gru's perspective. gru produces Jira tickets and Confluence pages; it does not write files outside its own workspace.

### Harness helpers

`.agents/skills/_lib/` holds shared Python helpers that gate scripts and the agent both call. Pure stdlib (no third-party deps), `__main__` CLI for shell-out, tests under `_lib/tests/`.

- **`markdown.py`** — section parser for PRD bodies (used by `validate_parent.py`, `propose_slices.py`, `write_jira_prd.py`).
- **`graphify.py`** — substring search + node/edge access against `graph.json`. Resolves `graphify.search` / `graphify.get_node` / `graphify.get_edges` action-plan tools without a separate MCP server. Each `graphify.<intent>` action emitted by a gate script translates 1:1 to a shell exec of this helper.
  - **Resolution order for `graph.json`**: explicit `--graph` flag > `$GRAPHIFY_GRAPH_JSON` env var > `~/payments-graph/graphify-out/graph.json` (default).
  - **Public Python API**: `search(text, *, limit=20, scope_repos=None, graph_path=None)`, `get_node(node_id, *, graph_path=None)`, `get_edges(from_id, *, graph_path=None)`, `aggregate(text, *, limit=20, scope_repos=None, graph_path=None)`.
  - **CLI**: `python _lib/graphify.py {search|get_node|get_edges|aggregate} [args]` — JSON to stdout; exit 0 on success, exit 2 on `GraphifyUnavailable` (for the lower-level intents).
  - **Fallback contract**: when no `graph.json` is reachable, `aggregate` returns `{"status": "needs_synthesis", "reason": ..., "hint": ...}` instead of raising, so the calling skill can fall back to PRD-only fixture synthesis (the F1 manual workaround). The lower-level `search` / `get_node` / `get_edges` raise `GraphifyUnavailable` for callers that want hard-fail behaviour.
  - **Tests**: `cd .agents/skills/_lib && uv run --extra dev pytest tests/`.
  - **Aggregation shape**: `aggregate` currently returns `{modules, edges}` (matches `prd-to-jira-issues`). `spike-and-report` needs `{nodes, edges, communities}` — the agent renames `modules` → `nodes` and synthesises `communities` from the PRD's named clusters until the helper grows a `--shape spike` flag.
- **`required_fields.py`** — parses the `## Required custom fields (PLTPM)` section of `.agents/jira-conventions.md` and injects the declared customfield values into every `atlassian.createJiraIssue` action plan. Without this, gru eats one HTTP 400 per createIssue attempt for every PLTPM-required customfield (the F1 demo on `PLTPM-21272` proved this — 1 retry on the parent and pre-empted 12 retries on the children).
  - **Public Python API**: `parse_required_fields(conventions_text) -> dict[field_id, FieldSpec]`, `inject_required_fields(args, fields, issue_type, overrides=None) -> dict`. `FieldSpec` is a frozen dataclass exposing `field_id`, `display_name`, `default_value`, `allowed_values`, `applies_to`, `wire_shape`.
  - **CLI**: `python _lib/required_fields.py parse --conventions-path <path>` — JSON to stdout (manual inspection only; emitter scripts call the Python API directly).
  - **Wire shape**: `object` shape renders as `{"value": "<chosen>"}` (single-select customfields like Activity Type); `scalar` shape renders as a bare string (free-text customfields).
  - **Override path**: `write_jira_prd.py --activity-type "<value>"` and `write_jira_children.py --activity-type "<value>"` propagate the override through every emitted createIssue. Children inherit the parent's choice automatically because both scripts read the same conventions file.
  - **Drift detection**: `python .agents/skills/write-a-prd/scripts/validate_required_fields.py --bootstrap` emits an action plan to fetch live PLTPM issue-type metadata; `--drift-check --metadata-fixture <path>` compares the conventions section to that fetched metadata and reports drift (new required fields not in conventions, stale conventions, allowed-values changes, applies-to changes).
  - **Tests**: `cd .agents/skills/_lib && uv run --extra dev pytest tests/test_required_fields.py` (helper unit tests). Gate tests live in `.agents/skills/write-a-prd/scripts/tests/test_validate_required_fields.py`.
