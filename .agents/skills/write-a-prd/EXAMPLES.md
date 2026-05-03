# write-a-prd worked examples

Two end-to-end walkthroughs. Both use the reference drafts in `scripts/tests/fixtures/`. The Jira keys (`PLTPM-99001`, `PLTPM-99002`) and Confluence page IDs are illustrative only — gru has not yet been run end-to-end against PLTPM.

---

## Example 1 — Inline PRD (single repo, user-facing actor)

**Slug**: `inline-merchant-health` ([draft](scripts/tests/fixtures/inline-merchant-health.md))

**Scenario**: A merchant-support PM wants the processor-health endpoint to surface per-merchant scope, plus a `DEGRADED` status with reason codes. Single repo (`payment-platform`), user-facing actors (merchants, agents, customers), small PRD.

### 1. Probe (graphify helper)

The agent searches graphify (via the `_lib/graphify.py` harness helper) with the PM's one-liner:

```text
graphify.search(query="processor health degraded reason code merchant scope", limit=20)
```

Top hits cluster on `payment-platform` modules: `ProcessorHealthClient`, `processor-health-cache`, the existing health enum. No hits in `walletapi`, `infrastructure`, or `spring-boot-starters`. The agent reports back:

> Likely affected app: **payment-platform** only (modules: `ProcessorHealthClient`, `processor-health-cache`, the health enum). No edges into walletapi or infra. Sound right?

PM confirms.

### 2. Narrate

The agent spot-checks `gru/knowledge/narrative/repos/payment-platform.md` for the relevant tribal-knowledge cues — none directly applicable. No narrative-flows file is relevant for this small change.

### 3–5. Interview → Append → Review

The agent interviews section by section. The PM iterates twice (re-wording the second user story) before saying "ship it". The final draft is committed to `drafts/inline-merchant-health.md` and matches the fixture.

### 6. Validate components

```text
$ python scripts/validate_components.py
```

Output:

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
  "next_step": "Save the response to a JSON file and re-invoke ..."
}
```

The agent calls the Atlassian MCP, saves the response to `/tmp/components.json`, and re-runs:

```text
$ python scripts/validate_components.py --components-fixture /tmp/components.json
```

Output: `{"status": "ok", "canonical": [...]}` — proceed.

### 7. Score significance

```text
$ python scripts/significance_check.py --draft drafts/inline-merchant-health.md
```

Output:

```json
{
  "decision": "inline",
  "reasons": [],
  "metrics": {
    "user_stories": 3,
    "cross_app_impact_entries": 1,
    "max_mermaid_lines": 0,
    "has_elevate_marker": false
  }
}
```

Saved to `drafts/inline-merchant-health.decision.json`.

### 8. Plan write

```text
$ python scripts/write_jira_prd.py \
    --draft drafts/inline-merchant-health.md \
    --decision-json drafts/inline-merchant-health.decision.json
```

Output (excerpted — full version is the golden at `scripts/tests/fixtures/inline-merchant-health.expected.json`):

```json
{
  "mode": "inline",
  "summary": "Add merchant scope to processor health checks",
  "issue_type": "Story",
  "primary_component": "payment-platform",
  "candidate_components": ["payment-platform"],
  "requires_user_choice": null,
  "actions": [
    {
      "step": 1,
      "tool": "atlassian.createJiraIssue",
      "args": {
        "projectKey": "PLTPM",
        "issueType": "Story",
        "components": ["payment-platform"],
        "summary": "Add merchant scope to processor health checks",
        "description": "## Problem Statement\n... (full draft body) ..."
      },
      "captures": "parent_issue_key"
    }
  ]
}
```

### 9. Execute

The agent calls the resolved MCP tool (e.g. `mcp_atlassian_createJiraIssue`) with `args` from step 1. Response includes `key: "PLTPM-99001"`. No substitutions or follow-up steps are needed in inline mode.

### 10. Cleanup

The agent posts in chat:

> Created **PLTPM-99001** (Story, Component `payment-platform`). Description has all 8 sections inline. Ready for `prd-to-jira-issues` when you are.

Then deletes `drafts/inline-merchant-health.md` and `drafts/inline-merchant-health.decision.json`.

**Total elapsed**: ~5 minutes including the PM interview.

---

## Example 2 — Elevated PRD (single repo, system actors, explicit marker)

**Slug**: `elevate-transfers-v3` ([draft](scripts/tests/fixtures/elevate-transfers-v3.md))

**Scenario**: A platform engineer-PM wants to refactor `TransferSaga`'s ACH-return handling for idempotency. Single repo (`payment-platform`), all system actors (`payment-platform`, `TransferSaga`, `SQS consumer`), and the PM dropped a `<!-- elevate-to-confluence -->` marker because they expect this to grow during grooming.

### 1. Probe

```text
graphify.search(query="TransferSaga ACH return idempotency duplicate", limit=20)
```

Hits cluster on `payment-platform`: `TransferSaga`, `ach-return` flow, the SQS consumer, the duplicate-detection metrics. The agent surfaces this and the PM confirms.

### 2. Narrate

The agent reads `gru/knowledge/narrative/flows/ach-return.md` to ground the interview in the actual flow (Payliance → SQS → task-worker → saga). It cites this in the `## References` section of the draft.

### 3–5. Interview → Append → Review

The PM is opinionated; the interview produces a tight draft in one pass. They drop the `<!-- elevate-to-confluence -->` marker themselves because they expect 3+ additional Implementation-Decisions bullets to land at grooming. Final draft matches the fixture.

### 6. Validate components

Same as Example 1 — `{"status": "ok"}`.

### 7. Score significance

```text
$ python scripts/significance_check.py --draft drafts/elevate-transfers-v3.md
```

Output:

```json
{
  "decision": "elevate",
  "reasons": ["explicit elevate-to-confluence marker present"],
  "metrics": {
    "user_stories": 3,
    "cross_app_impact_entries": 1,
    "max_mermaid_lines": 0,
    "has_elevate_marker": true
  }
}
```

The size thresholds aren't tripped — only the explicit marker. That's enough.

### 8. Plan write

```text
$ python scripts/write_jira_prd.py \
    --draft drafts/elevate-transfers-v3.md \
    --decision-json drafts/elevate-transfers-v3.decision.json
```

Output (excerpted; golden at `scripts/tests/fixtures/elevate-transfers-v3.expected.json`):

```json
{
  "mode": "elevate",
  "summary": "Transfers V3 internal saga overhaul for ACH return reliability",
  "issue_type": "Technical Story",
  "primary_component": "payment-platform",
  "candidate_components": ["payment-platform"],
  "requires_user_choice": null,
  "actions": [
    {
      "step": 1,
      "tool": "atlassian.createConfluencePage",
      "args": {
        "spaceKey": "PS",
        "title": "PRD: Transfers V3 internal saga overhaul for ACH return reliability",
        "body": "## Problem Statement\n... (full draft) ..."
      },
      "captures": "confluence_page_url"
    },
    {
      "step": 2,
      "tool": "atlassian.createJiraIssue",
      "args": {
        "projectKey": "PLTPM",
        "issueType": "Technical Story",
        "components": ["payment-platform"],
        "summary": "Transfers V3 internal saga overhaul for ACH return reliability",
        "description": "**This is a significant PRD; the full text lives in Confluence.**\n\n**Confluence**: <CONFLUENCE_PAGE_URL>\n\n## Problem Statement\n... (terse summary + repo list) ..."
      },
      "captures": "parent_issue_key",
      "substitutions": {"<CONFLUENCE_PAGE_URL>": "{{confluence_page_url}}"}
    },
    {
      "step": 3,
      "tool": "atlassian.addConfluenceRemoteLinkToJiraIssue",
      "args": {
        "issueKey": "{{parent_issue_key}}",
        "url": "{{confluence_page_url}}",
        "title": "PRD: Transfers V3 internal saga overhaul for ACH return reliability"
      }
    }
  ]
}
```

Note: parent type is `Technical Story` — all 3 user stories use code-shaped actors (`payment-platform`, `TransferSaga`, `SQS consumer`).

### 9. Execute

| Step | MCP call | Captured |
|---|---|---|
| 1 | `mcp_atlassian_createConfluencePage(spaceKey="PS", title="PRD: Transfers V3 ...", body=...)` | `confluence_page_url = https://moneylion.atlassian.net/wiki/spaces/PS/pages/9001/PRD-Transfers-V3-...` |
| 2 | Substitute `<CONFLUENCE_PAGE_URL>` → captured URL. Then `mcp_atlassian_createJiraIssue(...)`. | `parent_issue_key = PLTPM-99002` |
| 3 | Substitute `{{parent_issue_key}}` and `{{confluence_page_url}}`. Then `mcp_atlassian_createIssueRemoteLink(issueKey="PLTPM-99002", url="...", title="PRD: Transfers V3 ...")`. | — |

### 10. Cleanup

> Created **PLTPM-99002** (Technical Story, Component `payment-platform`). Full PRD elevated to Confluence: <https://moneylion.atlassian.net/wiki/spaces/PS/pages/9001/...>. Remote link wired up on the Jira issue. Ready for `prd-to-jira-issues`.

Drafts deleted.

**Total elapsed**: ~8 minutes including the elevate-path execution.

---

## Edge case: multi-repo PRD (`requires_user_choice`)

If `## Cross-Application Impact` references more than one canonical repo (e.g. both `payment-platform` and `walletapi`), `write_jira_prd.py` returns:

```json
{
  "mode": "inline",
  "summary": "...",
  "primary_component": null,
  "candidate_components": ["payment-platform", "walletapi"],
  "requires_user_choice": {
    "field": "primary_component",
    "prompt": "Cross-Application Impact references multiple canonical repos. Which repo owns the parent ticket?",
    "options": ["payment-platform", "walletapi"]
  },
  "actions": []
}
```

The agent asks the PM in chat. Once chosen, the agent edits the draft so only the chosen repo's bullet remains in `## Cross-Application Impact` (children for the other repo will come from `prd-to-jira-issues`), then re-runs `write_jira_prd.py`. The choice is preserved on the parent because the child-creation step reads the parent's Component when scoping per-repo slices.

(Future improvement: a `--primary-component <repo>` flag on `write_jira_prd.py` so the draft does not need editing. Tracked under the `demo` todo.)
