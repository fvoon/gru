# write-a-prd reference

Detailed reference for the agent and for engineers maintaining the skill. Read this when:

- you need the full PRD template (including the gru-specific additions);
- you need the JSON action-plan contract for one of the 3 scripts;
- you need example graphify helper queries for the up-front probe or on-demand follow-ups;
- you need the Atlassian MCP tool-name mapping for the action plans.

## PRD template

Sections must appear **in this order**. Headers must be exact (`## Problem Statement`, etc.) — `significance_check.py` and `write_jira_prd.py` split on them.

```markdown
# <Title — kebab-case slug derived from this becomes the draft filename>

<!-- Optional: drop the line below to force the elevate path. Otherwise the
     significance heuristic decides automatically. -->
<!-- elevate-to-confluence -->

## Problem Statement
The problem the user is facing, from the user's perspective. 1–3 paragraphs.

## Solution
The solution to the problem, from the user's perspective. Keep it user-facing
even when the solution is internal — implementation lives below.

## User Stories
A numbered list. Each story in the format
`<n>. As <a|an|the>? <actor>, I want <feature>, so that <benefit>.`
Drives the parent-type heuristic — see `Heuristics` below.

## Cross-Application Impact   <!-- gru addition -->
- payment-platform: <what changes, which modules, which events>
- walletapi: <...>
- infrastructure: <...>
- spring-boot-starters: <...>

The leading bullet token MUST be one of the 4 canonical repos. Children for
non-canonical repos (or domain concepts) come from `prd-to-jira-issues`, not
this PRD.

## Implementation Decisions
- Modules to be built / modified
- Interfaces of those modules
- Architectural decisions, schema changes, API contracts, specific interactions
- (Do NOT include file paths or code snippets — they go stale fast.)

## Out of Scope
What is explicitly excluded from this PRD. Use to prevent scope creep at grooming.

## Further Notes
Anything else: rollout strategy, feature-flag names, coordination notes, on-call
implications, etc.

## References   <!-- gru addition -->
- Confluence: <link if elevated, else "(none)">
- graphify wiki nodes: <list of node IDs / slugs from the up-front probe>
- Narrative flows: <list of files under gru/knowledge/narrative/flows/>
- Related ADRs: <list, or "(none)">
```

## Heuristics

### Parent type (Story vs Technical Story)

Implemented in `write_jira_prd.is_system_actor` + `detect_parent_type`. The script captures the actor phrase from each `<n>. As <a|an|the>? <actor>, ...` story, then:

- **Kebab-case** (contains `-`, e.g. `payment-platform`, `transfer-saga`) → system actor.
- **CamelCase** (internal capital letter, e.g. `TransferSaga`, `SQS consumer`) → system actor.
- Plain lowercase prose → user-facing actor.

If **all** captured actors are system → `Technical Story`. Otherwise (or no `As ...` stories at all) → `Story`. The PM can override at execute time by editing the draft.

### Significance (inline vs elevate)

Implemented in `significance_check.score`. Any of these triggers `elevate`:

| Trigger | Threshold |
|---|---|
| Numbered items under `## User Stories` | `>10` |
| Bullets under `## Cross-Application Impact` whose description is `>50` chars | `>3` |
| Any single mermaid fenced code block | `>30` lines (inner) |
| Literal `<!-- elevate-to-confluence -->` HTML comment in draft | present |

Boundary cases are inline: exactly 10 stories, exactly 3 long Cross-App entries, exactly 30-line mermaid blocks. The heuristic is conservative — when in doubt, stay inline (lower friction).

### Component derivation

Implemented in `write_jira_prd.detect_components`. Bullet prefixes in `## Cross-Application Impact` matching one of the 4 canonical repos are collected, deduplicated, and returned in canonical order (`payment-platform`, `walletapi`, `infrastructure`, `spring-boot-starters`).

- **0 canonical repos referenced** → `InvalidDraft` (exit 2). At least one must appear.
- **1 canonical repo** → `primary_component` set; plan ready.
- **2+ canonical repos** → `requires_user_choice` set; `actions` is empty. The agent must ask the PM which repo "owns" the parent and re-invoke (today: edit the draft to keep one canonical bullet; future: `--primary-component` flag).

## Graphify access recipes

Graphify queries are served by `.agents/skills/_lib/graphify.py`, which reads `graph.json` directly (`~/payments-graph/graphify-out/graph.json` by default; override via `$GRAPHIFY_GRAPH_JSON`). It exposes nodes per concept / module / file across the 4 canonical repos. No standalone MCP server is required.

### Up-front affected-apps probe

Use a single broad query to find which repos / modules surface most strongly for the PM's idea. Pseudocode:

```text
search_concepts(query="<PM's one-line idea>", limit=20)
  → group hits by repo
  → for each top hit: get_node_details(id=...)
  → summarize as a "likely affected apps + integration points" digest
```

Show the digest to the PM and ask "does this match what you're thinking? anything missing?" before deep-diving.

### On-demand follow-ups during the interview

When the interview hits a concept the PM mentions ambiguously:

```text
search_concepts(query="<concept>", limit=5)
  → if any hit lives in narrative flows / glossary, read those files instead of speculating
  → if hits are spread across multiple repos, surface that and ask which scope the PM means
```

When the PM names a specific module:

```text
get_node_details(id=<module-id>)
  → list its inbound + outbound edges
  → use to populate Cross-App Impact bullets accurately
```

The agent resolves these semantic intents via the `.agents/skills/_lib/graphify.py` harness helper (no graphify MCP server required). Mapping:

- `search_concepts(query, limit)` → `python _lib/graphify.py search --text "<query>" --limit <N>`
- `get_node_details(id)` → `python _lib/graphify.py get_node --id <id>` followed by `get_edges --from-id <id>` for the edge list.

Default graph: `~/payments-graph/graphify-out/graph.json` (override via `$GRAPHIFY_GRAPH_JSON`). When no `graph.json` is reachable, the higher-level `aggregate` subcommand returns `{"status": "needs_synthesis", ...}` and the SKILL falls back to narrative-knowledge probing (read `gru/knowledge/narrative/`); the lower-level intents above raise `GraphifyUnavailable` so the agent can detect the gap explicitly. See "Harness helpers" in gru's `AGENTS.md`.

## Action-plan JSON contracts

All 3 scripts write JSON to stdout. Exit codes: `0` success, `1` business-failure (e.g. components missing), `2` IO / parse / inconsistency.

### `validate_components.py`

**Default mode** (no `--components-fixture`):

```json
{
  "status": "needs_fetch",
  "canonical": ["payment-platform", "walletapi", "infrastructure", "spring-boot-starters"],
  "actions": [
    {
      "step": 1,
      "tool": "atlassian.getJiraProjectComponents",
      "args": {"projectKey": "PLTPM"},
      "purpose": "Fetch current Components in PLTPM..."
    }
  ],
  "next_step": "Save the response to a JSON file and re-invoke: validate_components.py --components-fixture <path>."
}
```

**Verify mode** (`--components-fixture <path>`):

```json
{"status": "ok", "canonical": [...]}
```

…or, if any canonical name is absent in the fixture (exit 1):

```json
{
  "status": "missing",
  "missing": ["walletapi", "infrastructure"],
  "canonical": [...],
  "fetched": ["payment-platform", "spring-boot-starters", "<extras>"],
  "remediation": "Create the missing Components manually under PLTPM admin: ..."
}
```

### `significance_check.py`

```json
{
  "decision": "inline" | "elevate",
  "reasons": ["<human-readable trigger>", ...],
  "metrics": {
    "user_stories": 4,
    "cross_app_impact_entries": 1,
    "max_mermaid_lines": 0,
    "has_elevate_marker": false
  }
}
```

`reasons` is empty for the inline path. Multiple triggers all show up — the script does not short-circuit.

### `write_jira_prd.py`

Common envelope:

```json
{
  "mode": "inline" | "elevate",
  "summary": "<H1 title, truncated to 252 chars + '...' if it exceeds 255>",
  "summary_exceeds_soft_limit": false,
  "issue_type": "Story" | "Technical Story",
  "primary_component": "payment-platform" | null,
  "candidate_components": ["payment-platform", ...],
  "requires_user_choice": null | { "field": "primary_component", "prompt": "...", "options": [...] },
  "actions": [...]
}
```

**Inline mode** — 1 action:

```json
{
  "step": 1,
  "tool": "atlassian.createJiraIssue",
  "args": {
    "projectKey": "PLTPM",
    "issueType": "Story",
    "components": ["payment-platform"],
    "summary": "<title>",
    "description": "<full draft minus H1 and elevate marker>"
  },
  "captures": "parent_issue_key"
}
```

**Elevate mode** — 3 actions in order:

1. `atlassian.createConfluencePage` — `args.body` is the full draft (minus H1, minus elevate marker). Captures `confluence_page_url`.
2. `atlassian.createJiraIssue` — `args.description` includes the literal placeholder `<CONFLUENCE_PAGE_URL>`. The `substitutions` field maps that placeholder to `{{confluence_page_url}}` (the value captured in step 1). Captures `parent_issue_key`.
3. `atlassian.addConfluenceRemoteLinkToJiraIssue` — `args.issueKey = "{{parent_issue_key}}"`, `args.url = "{{confluence_page_url}}"`. Establishes the Confluence remote link on the Jira issue.

When `requires_user_choice` is set, `actions` is `[]`. The agent asks the PM, then re-invokes the script.

## Atlassian MCP tool-name mapping

`tool` fields in action plans are **semantic intents**, not literal MCP tool names. The agent maps them to whichever Atlassian MCP is mounted (cloud, server, on-prem). Common mappings:

| Semantic intent | Likely MCP tool name(s) |
|---|---|
| `atlassian.getJiraProjectComponents` | `mcp_atlassian_getJiraProjectComponents`, `getProjectComponents` |
| `atlassian.createJiraIssue` | `mcp_atlassian_createJiraIssue`, `createIssue` |
| `atlassian.createConfluencePage` | `mcp_atlassian_createConfluencePage`, `createPage` |
| `atlassian.addConfluenceRemoteLinkToJiraIssue` | `mcp_atlassian_createIssueRemoteLink`, `addIssueWebLink`, `editIssue` (via `remotelinks`) |

### Variable substitution

The agent owns substitution. After step `n` runs, capture the value indicated by that step's `captures` field, then for each subsequent action:

1. Apply the action's `substitutions` map (literal-string → variable-name) to every string field in `args`.
2. Resolve `{{variable_name}}` placeholders in `args` to captured values.

Worked example for elevate mode:

```text
step 1 → createConfluencePage → response.url   ⇒  confluence_page_url = "https://moneylion.atlassian.net/wiki/spaces/PS/pages/12345"
step 2 → before MCP call:
  - replace "<CONFLUENCE_PAGE_URL>" in description with confluence_page_url
  - createJiraIssue → response.key            ⇒  parent_issue_key = "PLTPM-21500"
step 3 → before MCP call:
  - replace "{{parent_issue_key}}"   with "PLTPM-21500"
  - replace "{{confluence_page_url}}" with the URL captured in step 1
  - addConfluenceRemoteLinkToJiraIssue
```

If any step fails (auth, transient network, validation), retry once. If the second attempt fails, post the partial state to chat (e.g. "Confluence page created at <url>, Jira issue not yet created — please rerun") and stop. **Do not** delete the local draft until the full plan succeeds.

## Idempotency and re-runs

`write_jira_prd.py` is deterministic: given the same draft + mode, it produces byte-identical output. If the agent has to retry execution after a partial failure, it can re-run the script and compare to the previous output to confirm no draft drift. (The script prints `--mode` and the resolved `decision` so eyeballing is trivial.)

`significance_check.py` is also deterministic for the same draft.

`validate_components.py` is non-deterministic only across calls separated by Component edits in PLTPM (which is rare and a deliberate admin action).

## Failure modes and what to do

| Failure | Cause | Action |
|---|---|---|
| `validate_components.py` exits 1 | Canonical Components missing in PLTPM | Refuse. Point PM at `.agents/jira-conventions.md` bootstrap checklist. |
| `significance_check.py` exits 2 | Required section missing in draft | Tell the PM which section is missing; loop back to interview. |
| `write_jira_prd.py` exits 2, "no canonical repo" | Cross-App Impact has no canonical-repo bullet | Loop back to interview; restate the canonical-repo policy. |
| `write_jira_prd.py` returns `requires_user_choice` | Multi-canonical-repo PRD | Ask PM "which repo owns the parent?". Edit draft to leave only the chosen repo in Cross-App Impact (children cover the rest), re-run. |
| MCP tool 401/403 | Atlassian creds expired | Re-auth in the runtime; do not retry from the script. |
| Atlassian rate limit | Burst | Back off (exponential), retry once, fail loud if still 429. |
