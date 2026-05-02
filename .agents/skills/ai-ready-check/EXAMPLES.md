# ai-ready-check — examples

Three end-to-end walkthroughs covering the dominant code paths. Each shows the agent invocations, the action plans the script emits, and the resulting Jira-side outcome.

Refer to [REFERENCE.md](REFERENCE.md) for action-plan shapes and [SKILL.md](SKILL.md) for the workflow overview.

## Example 1 — ticket passes all checks (clean first run)

**Setup**: an engineer has triaged `PLTPM-21503` ("Wallet-to-wallet transfer event publisher"):

- Type: `Technical Story`
- Status: `next`
- Component: `payment-platform`
- Implement-link to parent `PLTPM-21000`
- Description includes `## Acceptance criteria` with three checklist items
- No `is blocked by` links
- No prior `ai-ready-check` comment
- No `ai-ready` label yet

User: "use the ai-ready-check skill on PLTPM-21503".

### Step 1 — phase 1 fetch

```
$ python validate_ai_ready.py --ticket-key PLTPM-21503
```

Stdout (`needs_fetch_ticket`): emits a single `getJiraIssue` action with `expand=comments,issuelinks,renderedFields`.

The agent calls `atlassian.getJiraIssue` and saves the response to `/tmp/ticket-21503.json`. The fixture under [`scripts/tests/fixtures/ticket-passes-all.json`](scripts/tests/fixtures/ticket-passes-all.json) shows what the response looks like for this scenario.

### Step 2 — phase 3 terminal (no blockers, jumps directly)

```
$ python validate_ai_ready.py --ticket-fixture /tmp/ticket-21503.json
```

Stdout (`ai_ready`): the byte-stable golden lives at [`scripts/tests/fixtures/ai-ready-pass.expected.json`](scripts/tests/fixtures/ai-ready-pass.expected.json). Two actions:

1. `atlassian.addCommentToJiraIssue` with the structured passing comment (marker + outcome JSON + `## ai-ready-check passed`).
2. `atlassian.editJiraIssue` with `update.labels[{add: ai-ready}]`.

### Step 3 — execute

The agent calls both MCP tools. The ticket now has the `ai-ready` label and a fresh comment. The dispatcher's pickup JQL (`status = next AND labels = ai-ready`) now matches `PLTPM-21503`.

### Re-run

If the engineer immediately re-invokes the skill:

```
$ python validate_ai_ready.py --ticket-fixture /tmp/ticket-21503-refetched.json
```

The refetched fixture now contains the fresh `ai-ready-check` comment. The script extracts the prior outcome via the marker, sees `prior_outcome == current_checks` AND `label_currently_set == True`, and emits `status: no_change` with empty `actions`. The agent reports "no change since the last run" and stops.

---

## Example 2 — ticket fails multiple checks

**Setup**: an engineer has just transitioned `PLTPM-21504` ("[BE] Wallet PIN reset endpoint") but it isn't actually ai-ready yet:

- Type: `Technical Story` (passes)
- Status: `To Do` (FAIL — not `next`)
- Components: `wrong-component`, `another-wrong` (FAIL — multiple, both non-canonical)
- No Implement link (FAIL)
- Description has no `## Acceptance criteria` section (FAIL — locked 2D)
- No blockers
- No prior `ai-ready-check` comment

User: "use the ai-ready-check skill on PLTPM-21504".

### Step 1 — phase 1 fetch

```
$ python validate_ai_ready.py --ticket-key PLTPM-21504
```

Stdout: `needs_fetch_ticket`. Same as Example 1.

### Step 2 — phase 3 terminal (no blockers)

```
$ python validate_ai_ready.py --ticket-fixture /tmp/ticket-21504.json
```

Stdout (`not_ai_ready`): byte-stable golden lives at [`scripts/tests/fixtures/ai-ready-multi-fail.expected.json`](scripts/tests/fixtures/ai-ready-multi-fail.expected.json). The fixture is [`ticket-fails-multi.json`](scripts/tests/fixtures/ticket-fails-multi.json).

`failed_items`: `["ac_present", "components_canonical", "implement_link", "status_next"]`.

The action plan has only one action — `atlassian.addCommentToJiraIssue` — because the label isn't currently set, so there's nothing to remove.

The comment body lists each failed item with a concrete remediation:

```
- **Acceptance criteria section missing or empty.** Add a `## Acceptance criteria`
  section to the ticket description with at least one checklist item.
- **Multiple Components set (`wrong-component`, `another-wrong`).** gru-managed
  tickets carry exactly one Component, and it must be one of: `payment-platform`,
  `walletapi`, `infrastructure`, `spring-boot-starters`.
- **No Implement link to a parent.** Add an `Implement` link outward from this
  ticket to its parent Story / Technical Story.
- **Status is `To Do`, expected `next`.** An engineer must transition this ticket
  `To Do → next` (transition id `221`) before it can be ai-ready.
```

### Step 3 — engineer fixes the items, re-runs

Now the engineer adds an AC section, swaps the Component to `walletapi`, links to a parent, and transitions to `next`. They re-run the skill:

```
$ python validate_ai_ready.py --ticket-fixture /tmp/ticket-21504-fixed.json
```

The script reads the prior `not_ai_ready` outcome from the comment, sees the current outcome differs (now all-pass), and emits `ai_ready` with comment + label-add. The fail comment from the prior run stays in the comment history; the new comment supersedes it as the most recent marker hit.

---

## Example 3 — ticket has blockers

**Setup**: `PLTPM-21507` is otherwise ai-ready but has two `is blocked by` links:

- `PLTPM-21000` (Done — already shipped)
- `PLTPM-21001` (In Progress — still open)

User: "use the ai-ready-check skill on PLTPM-21507".

### Step 1 — phase 1 fetch

Same as before — emits a single `getJiraIssue` for the ticket itself.

### Step 2 — phase 2 fetch blockers

```
$ python validate_ai_ready.py --ticket-fixture /tmp/ticket-21507.json
```

Stdout (`needs_fetch_blockers`):

```json
{
  "status": "needs_fetch_blockers",
  "ticket_key": "PLTPM-21507",
  "blocker_keys": ["PLTPM-21000", "PLTPM-21001"],
  "actions": [
    {"step": 1, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-21000"}},
    {"step": 2, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-21001"}}
  ]
}
```

The agent calls both `getJiraIssue` actions and saves the responses. The shapes match [`blocker-done.json`](scripts/tests/fixtures/blocker-done.json) and [`blocker-in-progress.json`](scripts/tests/fixtures/blocker-in-progress.json).

### Step 3 — phase 3 terminal

```
$ python validate_ai_ready.py \
    --ticket-fixture /tmp/ticket-21507.json \
    --blocker-fixtures /tmp/blocker-21000.json /tmp/blocker-21001.json
```

Stdout (`not_ai_ready`): `failed_items: ["blockers_done"]`. The comment body identifies the offending blocker:

```
- **Blockers not Done.** Still open: `PLTPM-21001` (In Progress).
```

If the agent's MCP tool call returns blocker fixtures whose key set doesn't match `blocker_keys` (e.g., the agent fetched only one of two), the script exits 1 with `invalid_input` so the agent retries with the right set. This is the locked-1A multi-phase-per-blocker contract.

### Re-run after the blocker ships

When `PLTPM-21001` reaches `Done`, the engineer re-invokes the skill. Phase 2 fetches both blockers fresh; phase 3 sees both Done, all checks pass, and emits `ai_ready` with comment + label-add.
