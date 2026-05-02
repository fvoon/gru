# prd-to-jira-issues — Worked examples

Two end-to-end walkthroughs that mirror the test goldens. Use these as a sanity check on your environment, or as a recipe for explaining the skill to a teammate.

## Example 1: single-repo inline parent → 1 child Task

**Setup**

- Parent: `PLTPM-99001` "Merchant-scoped processor health" (Story, Component `payment-platform`).
- Inline mode: full PRD lives in the parent description (no Confluence page).
- Single canonical repo named in `## Cross-Application Impact` → expect 1 child.

The corresponding fixtures (used by the golden tests) live in [`scripts/tests/fixtures/`](scripts/tests/fixtures/):

- [`parent-merchant-health.json`](scripts/tests/fixtures/parent-merchant-health.json)
- [`graphify-merchant-health.json`](scripts/tests/fixtures/graphify-merchant-health.json)
- [`slice-plan-merchant-health.md`](scripts/tests/fixtures/slice-plan-merchant-health.md) (raw output of `propose_slices`)
- [`slice-plan-merchant-health-redlined.md`](scripts/tests/fixtures/slice-plan-merchant-health-redlined.md) (after PM/eng fills in ACs)
- [`merchant-health.expected.json`](scripts/tests/fixtures/merchant-health.expected.json) (final action plan)

### Run-through

**Step 1: Validate parent**

```
$ python scripts/validate_parent.py --parent-key PLTPM-99001
{
  "status": "needs_fetch_parent",
  "actions": [{"step": 1, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-99001"}}]
}
```

The agent calls `atlassian.getJiraIssue(issueKey="PLTPM-99001")`, saves the response to `parent.json`, and re-runs the script:

```
$ python scripts/validate_parent.py --parent-key PLTPM-99001 --parent-fixture parent.json > validated.json
```

`validated.json` carries `status: "ok"`, `primary_component: "payment-platform"`, and the parsed sections.

**Step 2: Propose slices (phase 1)**

```
$ python scripts/propose_slices.py --parent-fixture validated.json
{
  "status": "needs_graphify",
  "actions": [
    {"step": 1, "tool": "graphify.search", "args": {"text": "<Implementation Decisions section>"}},
    {"step": 2, "tool": "graphify.get_node", "args": {"id": "<PER_HIT>"}},
    {"step": 3, "tool": "graphify.get_edges", "args": {"from_id": "<EACH_HIT>"}}
  ]
}
```

The agent runs the queries and aggregates results into `graphify.json`:

```json
{
  "modules": [
    {"name": "ProcessorHealthClient", "repo": "payment-platform"},
    {"name": "processor-health-cache", "repo": "payment-platform"}
  ],
  "edges": [{"from": "ProcessorHealthClient", "to": "processor-health-cache", "type": "calls"}]
}
```

**Step 3: Render the slice plan**

```
$ python scripts/propose_slices.py --parent-fixture validated.json \
    --graphify-fixture graphify.json \
    --out redlines/PLTPM-99001.md
{
  "status": "ok",
  "slice_count": 1,
  "types": {"Task": 1},
  "ordering_edges": [],
  "letters": ["A"],
  "path": "redlines/PLTPM-99001.md"
}
```

The agent opens [`redlines/PLTPM-99001.md`](scripts/tests/fixtures/slice-plan-merchant-health.md) and shows it to PM/eng.

**Step 4: PM/eng redlines the file**

The PM fills in 3 testable acceptance criteria (replacing the `<!-- TODO ... -->` placeholder and stub bullets) — see [`slice-plan-merchant-health-redlined.md`](scripts/tests/fixtures/slice-plan-merchant-health-redlined.md) for the result.

**Step 5: Plan children**

```
$ python scripts/write_jira_children.py \
    --slice-plan redlines/PLTPM-99001.md \
    --parent-key PLTPM-99001
{
  "status": "ok",
  "parent_key": "PLTPM-99001",
  "slice_count": 1,
  "letters": ["A"],
  "actions": [
    {"step": 1, "tool": "atlassian.createJiraIssue", ..., "stores_as": "slice_A_key"},
    {"step": 2, "tool": "atlassian.createJiraSubtask", "args": {"parentIssueKey": "{{slice_A_key}}", "summary": "Development", ...}},
    ...
    {"step": 6, "tool": "atlassian.createJiraSubtask", "args": {..., "summary": "Test case execution", ...}},
    {"step": 7, "tool": "atlassian.createIssueLink", "args": {"type": "Implement", "inwardIssue": "PLTPM-99001", "outwardIssue": "{{slice_A_key}}"}}
  ]
}
```

7 actions total. Phase 1 = 1 createIssue. Phase 2 = 5 Sub-tasks. Phase 3 = 1 Implement link. Phase 4 = 0 Blocks (no `depends_on:`).

**Step 6: Execute and post**

The agent calls each action in order, substituting `{{slice_A_key}}` with the real key returned from step 1. Final chat output:

```
Created children for PLTPM-99001:
  Slice A → PLTPM-99003 (Task, payment-platform)
    + 5 ceremony Sub-tasks
    + Implement link from PLTPM-99001

Run ai-ready-check next to flip the slice into the next column.
```

The agent then deletes `redlines/PLTPM-99001.md`.

## Example 2: multi-repo elevated parent → 2 Tasks + 1 Research

**Setup**

- Parent: `PLTPM-99002` "Transfers v3 ACH return reliability" (Technical Story, primary Component `payment-platform`).
- Elevated mode: parent description carries the Confluence URL; the full PRD lives in Confluence.
- `## Cross-Application Impact` lists 2 canonical repos (`payment-platform`, `walletapi`) → expect 2 Task slices.
- `## Implementation Decisions` includes `Spike: confirm SQS retry semantics ...` → expect 1 Research slice.

Fixtures: same naming pattern, `transfers-v3` instead of `merchant-health`. See [`parent-transfers-v3.json`](scripts/tests/fixtures/parent-transfers-v3.json) and friends.

### Run-through

**Step 1: Validate parent (3-phase due to elevation)**

```
$ python scripts/validate_parent.py --parent-key PLTPM-99002
# Phase 1 — agent fetches parent, saves to parent.json

$ python scripts/validate_parent.py --parent-key PLTPM-99002 --parent-fixture parent.json
{
  "status": "needs_fetch_confluence",
  "confluence_url": "https://moneylion.atlassian.net/wiki/spaces/ENG/pages/123456/Transfers-v3",
  "actions": [{"step": 1, "tool": "atlassian.getConfluencePage", "args": {"url": "..."}}]
}
# Agent fetches the Confluence body, saves to confluence.json

$ python scripts/validate_parent.py --parent-key PLTPM-99002 \
    --parent-fixture parent.json \
    --confluence-fixture confluence.json > validated.json
```

`validated.json`'s `full_prd_text` is now the Confluence body, and `is_elevated: true`.

**Step 2-3: Slice proposal**

After running the graphify queries, [`graphify-transfers-v3.json`](scripts/tests/fixtures/graphify-transfers-v3.json) carries:

```json
{
  "modules": [
    {"name": "TransferSaga", "repo": "payment-platform"},
    {"name": "SqsConsumer", "repo": "payment-platform"},
    {"name": "WalletLedgerClient", "repo": "walletapi"}
  ],
  "edges": [
    {"from": "SqsConsumer", "to": "TransferSaga"},
    {"from": "TransferSaga", "to": "WalletLedgerClient"}
  ]
}
```

`propose_slices.py` produces 3 slices in this order:

- **A**: `payment-platform` (Task) — TransferSaga + SqsConsumer. Depends on B (TransferSaga calls WalletLedgerClient → cross-repo edge).
- **B**: `walletapi` (Task) — WalletLedgerClient. No dependencies.
- **C**: `payment-platform` (Research) — Spike from `## Implementation Decisions`.

The summary JSON:

```json
{
  "status": "ok",
  "slice_count": 3,
  "types": {"Task": 2, "Research": 1},
  "ordering_edges": [{"from": "A", "to": "B"}],
  "letters": ["A", "B", "C"]
}
```

**Step 4: PM/eng redlines**

See [`slice-plan-transfers-v3-redlined.md`](scripts/tests/fixtures/slice-plan-transfers-v3-redlined.md). PM/eng:
- Fills in 3 ACs per Task slice (saga state machine, walletapi unique index, metrics).
- Fills in 2 ACs for the Research slice (findings comment + linked follow-up).
- Reviews the `depends_on: B` on slice A — confirms it's correct because the saga's idempotency guard relies on walletapi's deduped reversal endpoint.

**Step 5-6: Plan and execute**

```
$ python scripts/write_jira_children.py --slice-plan redlines/PLTPM-99002.md --parent-key PLTPM-99002
```

Action count: `3 createIssue + 5×2 Sub-tasks + 3 Implement links + 1 Blocks = 17 actions`.

Phase ordering (verified by the test goldens):

1. createIssue A, B, C (in that order; each stores `slice_<letter>_key`).
2. Sub-tasks for A (5), then Sub-tasks for B (5). C is a Research slice — skipped.
3. Implement A→parent, Implement B→parent, Implement C→parent.
4. `Blocks` link: `inwardIssue={{slice_B_key}}, outwardIssue={{slice_A_key}}` (slice A is blocked by B).

Final chat output:

```
Created children for PLTPM-99002:
  Slice A → PLTPM-99004 (Task, payment-platform) + 5 Sub-tasks
  Slice B → PLTPM-99005 (Task, walletapi) + 5 Sub-tasks
  Slice C → PLTPM-99006 (Research, payment-platform)
  Implement links: parent → A, B, C
  Blocks chain: B blocks A

Run ai-ready-check next.
```

## Edge cases worth knowing

- **Single-repo parent that needs splitting**: if the graphify modules for one repo form ≥2 weakly-connected components, `propose_slices.py` proposes a split. PM/eng can collapse the split in the redline by deleting one section and absorbing its content into the other.
- **Repeated re-render**: re-running `propose_slices.py` against an existing redline file refuses without `--force`. This protects in-progress PM edits.
- **Research without a PM-supplied AC**: the AC gate (`literal TODO`) catches this; PM/eng must add at least the "findings documented in a comment" criterion before `write_jira_children.py` will emit actions.
- **Partial failure mid-execution**: re-run `write_jira_children.py`. The output is byte-stable for the same redline, and the LLM filters already-succeeded steps from chat context (Issue 5B locked decision — see [REFERENCE.md](REFERENCE.md)).
