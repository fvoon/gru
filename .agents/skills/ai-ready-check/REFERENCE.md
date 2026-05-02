# ai-ready-check — reference

In-depth contract for the skill. Owner: see [SKILL.md](SKILL.md).

## Constants

Locked at sentinel test (per locked decision 3C — `CANONICAL_COMPONENTS` and `AI_READY_LABEL` MUST appear verbatim in [`.agents/jira-conventions.md`](../../jira-conventions.md)):

| Constant | Value |
|----------|-------|
| `CANONICAL_COMPONENTS` | `payment-platform`, `walletapi`, `infrastructure`, `spring-boot-starters` |
| `AI_READY_LABEL` | `ai-ready` |

Not sentinel-locked (Atlassian-stable globals + gru workflow names):

| Constant | Value |
|----------|-------|
| `ALLOWED_ISSUE_TYPES` | `Task`, `Technical Story` |
| `REQUIRED_STATUS` | `next` |
| `IMPLEMENT_LINK_TYPE` | `Implement` |
| `BLOCKER_LINK_TYPE` | `Blocks` |
| `DONE_STATUS` | `Done` |
| `COMMENT_MARKER` | `<!-- ai-ready-check:v1 -->` |

`COMMENT_OUTCOME_RE` matches `<!-- ai-ready-outcome: {…JSON…} -->`.

## Checklist (6 items)

Run in this fixed order; the outcome JSON keys mirror this order so the marker stays byte-stable:

| Key | Description | Pass condition |
|-----|-------------|----------------|
| `ac_present` | AC section presence (locked 2D) | `## Acceptance criteria` exists in description and has non-whitespace content |
| `blockers_done` | Blockers all Done | every `is blocked by` blocker has `status.name == "Done"` (vacuously true if none) |
| `components_canonical` | Single canonical Component | `len(components) == 1 and components[0] in CANONICAL_COMPONENTS` |
| `implement_link` | Parent linked | at least one `Implement` link to a parent |
| `status_next` | Status is `next` | `status.name == "next"` |
| `issue_type_allowed` | Allowed type | `issuetype.name in {"Task", "Technical Story"}` |

## Phase ladder

The script is a pure compute layer. The LLM is the executor; the script tells it what to do via JSON action plans.

```
Phase 1: needs_fetch_ticket    → emit one getJiraIssue
Phase 2: needs_fetch_blockers  → emit one getJiraIssue per blocker
Phase 3: terminal              → emit one of:
                                 - ai_ready      (pass + outcome changed)
                                 - not_ai_ready  (fail + outcome changed OR label state wrong)
                                 - no_change     (outcome matches prior + label state correct)
```

Phase 2 is skipped when the ticket has zero `is blocked by` links.

## Action-plan shapes

### Phase 1 — `needs_fetch_ticket`

```json
{
  "status": "needs_fetch_ticket",
  "ticket_key": "PLTPM-21503",
  "actions": [
    {
      "step": 1,
      "tool": "atlassian.getJiraIssue",
      "args": {"issueKey": "PLTPM-21503", "expand": "comments,issuelinks,renderedFields"},
      "purpose": "..."
    }
  ],
  "next_step": "..."
}
```

### Phase 2 — `needs_fetch_blockers`

```json
{
  "status": "needs_fetch_blockers",
  "ticket_key": "PLTPM-21503",
  "blocker_keys": ["PLTPM-9000", "PLTPM-9001"],
  "actions": [
    {"step": 1, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-9000"}, "purpose": "..."},
    {"step": 2, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-9001"}, "purpose": "..."}
  ],
  "next_step": "..."
}
```

The supplied set of blocker fixtures must EXACTLY match `blocker_keys` (no missing, no extras). Mismatch is `invalid_input` (exit 1).

### Phase 3 — terminal

`ai_ready` — all 6 checks pass and outcome differs from prior:

```json
{
  "status": "ai_ready",
  "ticket_key": "PLTPM-21503",
  "checks": {"ac_present": true, "blockers_done": true, "components_canonical": true,
             "implement_link": true, "status_next": true, "issue_type_allowed": true},
  "outcome_changed": true,
  "label_currently_set": false,
  "actions": [
    {"step": 1, "tool": "atlassian.addCommentToJiraIssue",
     "args": {"issueKey": "PLTPM-21503", "body": "<!-- ai-ready-check:v1 -->\n..."}},
    {"step": 2, "tool": "atlassian.editJiraIssue",
     "args": {"issueKey": "PLTPM-21503", "update": {"labels": [{"add": "ai-ready"}]}}}
  ]
}
```

`not_ai_ready` — at least one fail:

```json
{
  "status": "not_ai_ready",
  "ticket_key": "PLTPM-21503",
  "checks": {...},
  "outcome_changed": true,
  "label_currently_set": true,
  "actions": [
    {"step": 1, "tool": "atlassian.addCommentToJiraIssue", "args": {...}},
    {"step": 2, "tool": "atlassian.editJiraIssue",
     "args": {"issueKey": "PLTPM-21503", "update": {"labels": [{"remove": "ai-ready"}]}}}
  ],
  "failed_items": ["ac_present", "components_canonical"]
}
```

`no_change` — outcome AND label state both match prior:

```json
{
  "status": "no_change",
  "ticket_key": "PLTPM-21503",
  "checks": {...},
  "prior_outcome": {...},
  "label_currently_set": true,
  "actions": []
}
```

### Label-edit semantics

`update.labels[{add|remove}]` is the merge-safe Jira REST shape — it adjusts the label list without overwriting other labels. The action is **only** emitted when the current label state disagrees with the desired state:

| desired (passed) | currently set | label action |
|------------------|---------------|--------------|
| `true` | `false` | `add` |
| `true` | `true` | (omitted) |
| `false` | `true` | `remove` |
| `false` | `false` | (omitted) |

This means a passing ticket that already has `ai-ready` will get only the comment refreshed; a failing ticket without the label will get only the comment posted. Cross-cuts cleanly with the no-change short-circuit.

## Comment marker spec

Every emitted comment starts with two HTML-comment lines:

```
<!-- ai-ready-check:v1 -->
<!-- ai-ready-outcome: {"ac_present":true,"blockers_done":true,...} -->

## ai-ready-check passed | failed
...
```

- The first line is the marker. Versioned so a future format bump (`v2`) doesn't false-positive against old comments.
- The second line embeds the per-check booleans as compact JSON, key-ordered to match `CHECK_KEYS` (insertion order, no sort), so the outcome JSON is byte-stable for goldens.
- `extract_prior_outcome` walks comments in reverse order (Atlassian returns oldest-first), matches the marker, parses the JSON, and coerces values to bool. Any malformed prior (missing keys, bad JSON, wrong shape) is treated as "no prior" so a re-run after a marker bump always emits a fresh comment.

## Re-run idempotency (locked 4B)

A re-run is a **no_change** when, and only when:

1. `prior_outcome != None` — there is a parseable prior `ai-ready-check` comment, AND
2. `prior_outcome == current_checks` — every check has the same boolean as last time, AND
3. `label_currently_set == desired_label_state` — the `ai-ready` label is in the right state for the current outcome.

If any of (1)-(3) is false, the script emits a fresh comment + label edit. Specifically:

- New ticket, never run before → first run emits comment.
- Outcome flipped (a check now passes that didn't, or vice versa) → fresh comment.
- Outcome same but label was manually toggled (e.g., a human removed it) → fresh comment + label fix.
- Marker version bumped (e.g., a `v2` skill releases) → re-runs treat old `v1` as "no prior" → fresh comment.

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `invalid_input: ticket payload missing `key`` | Wrong file passed as `--ticket-fixture` | Re-fetch with `atlassian.getJiraIssue` |
| `invalid_input: description is not a markdown string` | Atlassian returned ADF (default) instead of markdown | Re-fetch with `expand=renderedFields` and convert ADF→md before invoking |
| `invalid_input: blocker fixtures don't match...` | Supplied wrong blocker set | Match `--blocker-fixtures` to `blocker_keys` exactly |
| `error: either --ticket-fixture or --ticket-key must be provided` (exit 2) | First-run invocation without either flag | Pass `--ticket-key PLTPM-XXXXX` |
| Skill posts comment but label doesn't toggle | MCP rejected the `update.labels` shape (e.g., outdated MCP version) | Confirm the MCP supports REST `update`; fall back to `fields.labels` only if absolutely necessary |

## Maintenance

- **Adding a check**: extend `CHECK_KEYS`, write the check function, add a per-key failure-detail branch in `_render_failure_detail`, and bump `COMMENT_MARKER` to `v2` (so prior `v1` comments don't false-match).
- **Adding a canonical Component**: edit [`.agents/jira-conventions.md`](../../jira-conventions.md) `## Components` section AND `CANONICAL_COMPONENTS` in `validate_ai_ready.py`. The sentinel test pins the two together.
- **Renaming `ai-ready`**: edit `## Labels` section AND `AI_READY_LABEL`. Sentinel test enforces.
- **Re-running goldens**: `python validate_ai_ready.py --ticket-fixture tests/fixtures/ticket-passes-all.json > tests/fixtures/ai-ready-pass.expected.json` (and the same for the multi-fail fixture). Tests assert byte-equal.
