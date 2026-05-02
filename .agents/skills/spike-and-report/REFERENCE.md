# spike-and-report — Reference

Detailed contracts and conventions for the three deterministic scripts and the human-in-the-loop redlined report. See [SKILL.md](SKILL.md) for the high-level workflow and [EXAMPLES.md](EXAMPLES.md) for worked end-to-end runs.

## 7-section report template

The publish format. `bootstrap_worktree.py` writes the skeleton; the agent fills in the `TODO` placeholders during investigation; the engineer redlines; `write_spike_report.py` validates + publishes.

```markdown
# Spike report — <RESEARCH-KEY>

## Question

**Spike: <one-liner question>?**

<longer prompt with failure modes / scope / success criteria>

## Existing state (from graphify)

<auto-populated by write_spike_report.py — leave as-is during investigation>

## What was tried

- Approach A: <one line>. Verified by reading commit `<sha>` on the spike branch.
- Approach B: <one line>. Code excerpt from `<File>.java`:

  ```java
  // ≤30 lines per excerpt; not full file dumps
  ```

## What worked / what didn't

- <bullet>
- <bullet>

## Recommended approach

<one paragraph>

### Recommended contract changes

- <only if API / event / DB shapes are affected; otherwise omit this subsection>

## Suggested follow-up tickets

- `[BE] <title>` — <one-line AC>
- `[BE] <title>` — <one-line AC>

## Spike metadata

<auto-populated by write_spike_report.py at publish time>
```

### Section-level rules (enforced by `write_spike_report.py`)

- All 7 `## ` headings must be present (exact text). Missing → `status: invalid_report`.
- Sections **Question**, **What was tried**, **What worked / what didn't**, **Recommended approach**, **Suggested follow-up tickets** must be `TODO`-free at publish time. The literal token `TODO` (word boundary) anywhere in those sections fails validation with `status: todo_remaining`.
- Sections **Existing state (from graphify)** and **Spike metadata** are auto-populated. The publish step overwrites their bodies; the script does not gate on `TODO` there.
- The first line of the **Question** body becomes the Confluence page title (`Spike: <first line>`). A leading `Spike:` is stripped to avoid `Spike: Spike: ...`.
- Code excerpts go inside fenced code blocks. The markdown→Confluence converter handles language tags (`java`, `python`, etc.) by emitting an `ac:structured-macro` `code` block.

## Action-plan JSON contracts

All three scripts emit JSON to stdout. The LLM agent in chat is the executor — none of the scripts call MCP tools or shell commands directly.

### `validate_spike_target.py`

Multi-phase output. Status field tells the LLM which phase emitted the plan.

```json
// Phase 1: needs_fetch_research
{"status": "needs_fetch_research",
 "actions": [{"step": 1, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-21500"}}]}

// Phase 2: needs_fetch_parent (only if Research ticket has a parent)
{"status": "needs_fetch_parent", "parent_key": "PLTPM-21000",
 "actions": [{"step": 1, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-21000"}}]}

// Phase 3: needs_fetch_confluence (only if parent is elevated)
{"status": "needs_fetch_confluence", "confluence_url": "...",
 "actions": [{"step": 1, "tool": "atlassian.getConfluencePage", "args": {"url": "..."}}]}

// Phase 4: needs_repo_selection
{"status": "needs_repo_selection",
 "candidate_repos": ["payment-platform", "walletapi"],
 "next_step": "Re-run with --repos payment-platform,walletapi"}

// Phase 5: needs_graphify
{"status": "needs_graphify",
 "actions": [{"step": 1, "tool": "graphify.search", "args": {"text": "...", "scope": {"repos": [...]}}}, ...]}

// Phase 6: ok (the input to bootstrap_worktree.py)
{"status": "ok",
 "research_key": "PLTPM-21500",
 "research_summary": "...",
 "research_question": "...",
 "parent_key": "PLTPM-21000",
 "confluence_parent_page_id": "3333",
 "confluence_parent_path": "elevated-parent",   // or "inline-parent" / "orphan"
 "spikes_parent_page_id": "9876543",
 "confluence_space_key": "PLTPM",
 "confluence_space_id": "1048576",
 "repos": ["payment-platform", "walletapi"],
 "graphify": {"nodes": [...], "edges": [...], "communities": [...]}}
```

Idempotency refusals (exit 2; status fields):
- `existing_redline` — `redlines/spike-<KEY>-*.md` already exists.
- `existing_worktree` — `<worktree-base>/spike/<KEY>/` exists with content.
- `existing_state` — `.spike-state.json` exists at the worktree root.
- `existing_confluence_link` — Research ticket comments already include a Confluence URL pointing at a published spike page.
- `done_status` — Research ticket is already in `Done` status.

Exit codes: `0` (any phase emitted), `1` (validation failed — `status: "invalid"` on stdout), `2` (IO/parse/idempotency refusal — message on stderr).

### `bootstrap_worktree.py`

Single-phase output. The action plan is deterministic and ordered:

| Step group | Type | Per repo | Notes |
|---|---|---|---|
| Reset (only with `--reset-worktree`) | shell | 2 | `git worktree remove --force`, then `git branch -D spike/<KEY>`. |
| 1. Worktree create | shell | 2 | `git worktree add -B spike/<KEY> <wt-path>`, then `git config --local push.default nothing`. |
| 2. State file | write_file | 1 (root) | `<worktree-base>/spike/<KEY>/.spike-state.json`. |
| 3. SPIKE_README | write_file | 1 | Per-worktree no-PR warning + cleanup commands. |
| 4. Briefing | write_file | 1 (redline-dir) | `redlines/spike-<KEY>-briefing.md` — read-only agent context. |
| 5. Report skeleton | write_file | 1 (redline-dir) | `redlines/spike-<KEY>-report.md` — 7-section template with `TODO` placeholders. |

Steps are 1-indexed and consecutive across the whole plan.

```json
{"status": "ready",
 "research_key": "PLTPM-21500",
 "branch": "spike/PLTPM-21500",
 "worktree_root": "/Users/.../IdeaProjects/spike/PLTPM-21500",
 "redline_dir": "/Users/.../redlines",
 "actions": [
   {"step": 1, "type": "shell", "command": ["git", "-C", "...", "worktree", "add", "-B", "spike/PLTPM-21500", "..."], "purpose": "..."},
   {"step": 2, "type": "shell", "command": ["git", "-C", "...", "config", "--local", "push.default", "nothing"], "purpose": "..."},
   {"step": 3, "type": "write_file", "path": ".../.spike-state.json", "contents": "{...}", "purpose": "..."}
   // ... and so on per repo, ending with briefing + report skeleton
 ]}
```

Exit codes: `0` (plan emitted), `2` (IO/parse error or existing state/worktree without `--reset-worktree`).

### `write_spike_report.py`

Single-phase output (3 actions = 3 phases of MCP work):

| Phase | Tool | Notes |
|---|---|---|
| 1. publish | `atlassian.createConfluencePage` (or `updateConfluencePage` with `--update-existing`) | `parentPageId` from state file; `body.storage.value` is converted XHTML; `title` = `Spike: <Question first line>` (deduped). |
| 2. comment | `atlassian.addCommentToJiraIssue` | Substitutes `{{confluence_page_url}}` from phase 1's response. References `SPIKE_README.md` for cleanup. |
| 3. transition | `atlassian.transitionJiraIssue` | Carries the **target status name** (`Done`); the agent must call `atlassian.getJiraIssueTransitions` first to resolve to a transition id (workflow varies). |

Exit codes: `0` (plan emitted), `1` (report invalid — missing section / `TODO` remaining; `status` field on stderr), `2` (IO error / state-file error / unparseable `--now`).

## Markdown→Confluence storage-format converter

In-script and intentionally minimal. Supports only what the 7-section template needs:

| Markdown | Confluence storage |
|---|---|
| `## Heading` | `<h2>Heading</h2>` |
| `### Subheading` | `<h3>Subheading</h3>` |
| `- bullet` (also `*` / `+`) | `<ul><li>bullet</li></ul>` |
| Paragraph (consecutive non-blank lines) | `<p>line one line two</p>` |
| ` ```lang\nbody\n``` ` | `<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">lang</ac:parameter><ac:plain-text-body><![CDATA[body]]></ac:plain-text-body></ac:structured-macro>` |
| `` `inline code` `` | `<code>inline code</code>` |
| `[label](url)` | `<a href="url">label</a>` |
| `> quote` | `<blockquote>quote</blockquote>` |

Special chars (`<`, `>`, `&`, `"`) are XML-escaped. Anything outside these patterns gets wrapped in `<p>` and escaped (defensive). Confluence panels, expand macros, status macros, etc. are NOT supported — pasting them into the redline will render as escaped literal text.

## State file shape

`.spike-state.json` lives at `<worktree-base>/spike/<KEY>/.spike-state.json`. Required keys (validated by `write_spike_report.py`):

```json
{"schema_version": 1,
 "started_at": "2026-05-02T18:00:00+00:00",
 "research_key": "PLTPM-21500",
 "research_summary": "...",
 "parent_key": "PLTPM-21000",   // null for orphan spikes
 "confluence_parent_page_id": "3333",
 "confluence_parent_path": "elevated-parent",
 "spikes_parent_page_id": "9876543",
 "confluence_space_key": "PLTPM",
 "confluence_space_id": "1048576",
 "repos": ["payment-platform", "walletapi"],
 "worktree_paths": {"payment-platform": "...", "walletapi": "..."},
 "wall_clock_budget_minutes": 30,
 "turn_budget": 30}
```

`write_spike_report.py` reads this for: parent page id (publish phase), research key (comment + transition phases), started_at (elapsed-minutes computation), repos (metadata footer).

## Confluence parent resolution (3-level walk)

Implemented in `validate_spike_target.py`; mirrors `.agents/jira-conventions.md`'s `## Confluence` section.

| Path | When | Parent page id |
|---|---|---|
| `elevated-parent` | Research ticket has a parent ticket AND that parent is elevated (has its own Confluence page). | The elevated parent's page id. |
| `inline-parent` | Research ticket has a parent ticket but the parent is inline (no Confluence page). | The configured `Spikes parent page id` from `jira-conventions.md`. |
| `orphan` | Research ticket has no parent. | The configured `Spikes parent page id` from `jira-conventions.md`. |

The `Spikes parent page id` and `Spikes parent page title` are bootstrap-required values in `jira-conventions.md`. The script refuses to run if they are still `<TBA — bootstrap>`.

## `--update-existing` semantics

For controlled overwrites of a previously-published spike page (e.g. the engineer wants to revise after an external review). Requires `--existing-page-id <pageId>`. The agent must:

1. Resolve the existing page id by inspecting the Research ticket's previous Confluence comment (or the page's URL).
2. Pass it explicitly. The script does not auto-discover.
3. Re-publishing transitions the Research ticket to Done again, which is a no-op if it's already Done.

This intentionally does NOT bypass the redline + TODO-free gates. Re-publish flows still go through the same review checkpoint.

## graphify MCP call patterns

Same generic three-step plan as `prd-to-jira-issues` but scoped to the user-selected repos:

- `graphify.search` — full-text query derived from the Research ticket's question. `args.scope.repos` carries the `--repos` selection.
- `graphify.get_node` — resolve hit IDs to canonical names + repo metadata. Required for the `nodes` array.
- `graphify.get_edges` — outbound edges per hit. Required for the `edges` array.

Aggregate into `{nodes, edges, communities}` and pass back via `--graphify-fixture`.

## Atlassian MCP call patterns

Per `.agents/jira-conventions.md`:

- `getJiraIssue` — used for both the Research ticket fetch and the parent fetch.
- `getConfluencePage` — used for the elevated parent fetch (only when `confluence_parent_path = "elevated-parent"`).
- `createConfluencePage` / `updateConfluencePage` — phase 1 of `write_spike_report`. Body is `{storage: {value: <xhtml>, representation: "storage"}}`.
- `addCommentToJiraIssue` — phase 2. Body string contains the literal token `{{confluence_page_url}}` for the agent to substitute from phase 1's response.
- `transitionJiraIssue` — phase 3. Carries `transition.name = "Done"`; the agent resolves to an id via `getJiraIssueTransitions(issueKey)` because the workflow's transition ids are not stable across projects.

## Idempotency model

Three-tier:

1. **Filesystem-side** (`validate_spike_target.py`): refuses to start a spike if a redline / worktree / state file already exists.
2. **Comment-side** (`validate_spike_target.py`): refuses to start a spike if the Research ticket's comments already carry a Confluence URL pointing at a published spike page.
3. **Status-side** (`validate_spike_target.py`): refuses to start a spike if the Research ticket is already in `Done`.

For partial-failure resume during `write_spike_report.py`'s 3-phase publish, the model matches `prd-to-jira-issues` (Issue 5B): the script always emits the full action plan; the LLM filters succeeded steps from chat context. Cost of an over-create is low (the engineer can delete the duplicate Confluence page or revert the Jira comment).

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `validate_spike_target.py` exits 2 with `existing_redline` | Prior spike on this Research ticket left files behind | Delete `redlines/spike-<KEY>-*.md` if you want a clean re-run, or pass `--force` if you really want to start over. |
| Exits 2 with `existing_worktree` | Prior worktree exists and is non-empty | `git worktree remove --force` it, or pass `--reset-worktree` to `bootstrap_worktree.py`. |
| Exits 2 with `existing_confluence_link` | A Confluence page was already published for this ticket | If revising, use `write_spike_report.py --update-existing --existing-page-id <id>`. Otherwise this spike is done; pick a different ticket. |
| Exits 2 with `done_status` | Research ticket is already Done | Pick a different ticket. |
| Exits 1 with `not a Research-typed ticket` | Wrong issue type | gru's `spike-and-report` only operates on `Research` (with or without trailing space). Use `prd-to-jira-issues` for Tasks / Stories. |
| Exits 2 with `Confluence section in jira-conventions.md is unbootstrapped` | `<TBA — bootstrap>` placeholder still in the conventions file | Edit `.agents/jira-conventions.md`'s `## Confluence` section with real space + Spikes-parent ids. |
| `write_spike_report.py` exits 1 with `todo_remaining` | Engineer forgot to fill in a section | Open the redline, replace the literal `TODO` with real content. The error response lists offending section names. |
| Exits 1 with `missing required section(s)` | Engineer accidentally renamed a heading | Restore the canonical 7 headings (case-sensitive, exact text). |
| Phase-2 comment fails after phase-1 succeeded | Atlassian rate limit or transient error | Re-run; the LLM filters phase 1 from chat context. The Confluence page is idempotent at the page-id level only via `--update-existing`. |

## Maintenance

- `CANONICAL_REPOS` in `validate_spike_target.py` and `bootstrap_worktree.py` must stay in lockstep with `.agents/jira-conventions.md`. The sentinel test in `tests/test_validate_spike_target.py::TestSentinelAgainstConventions` enforces this.
- `WALL_CLOCK_BUDGET_MINUTES` and `TURN_BUDGET` are duplicated in `bootstrap_worktree.py`, `write_spike_report.py`, and the SKILL.md prose. Update all three together.
- `_lib/markdown.py` is shared with `write-a-prd` and `prd-to-jira-issues`. Refactors there must keep all three skills' tests green.
- The 7 required-section names live in both `bootstrap_worktree.py` (skeleton emitter) and `write_spike_report.py` (`REQUIRED_SECTIONS`). They must be byte-identical.
