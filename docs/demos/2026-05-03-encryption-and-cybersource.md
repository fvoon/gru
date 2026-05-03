# Demo run — 2026-05-03 — encryption-starter dual-write + Cybersource VGS network tokens

> Live two-feature demo of the [gru](https://github.com/MoneyLion/gru) harness against PLTPM. Captures the first end-to-end exercises of `write-a-prd` + `prd-to-jira-issues` (and `spike-and-report` for feature 2) on real PMR-shaped seeds. The plan that produced this run is at `.cursor/plans/demo_encryption_cybersource_be541aba.plan.md` (local).

## Context

- **Driver**: Felix (engineer + PM roleplay).
- **Agent**: Cursor agent in `payment-platform` workspace; gru skills loaded from `~/IdeaProjects/gru/.agents/skills/`.
- **Target Jira project**: PLTPM. Confluence space: `PS1` (Payments). Spike folder: `15 - Spikes` (id `6711345211`).
- **Skills exercised**: `write-a-prd`, `prd-to-jira-issues`, and (F2 only) `spike-and-report`. `ai-ready-check` is intentionally deferred — it requires an engineer Gate B (`To Do → next` transition) which we do not run in this session.

## Concurrency posture

Single agent, sequential features. F1 ran to completion (parent + children created, log entry filled in) before F2 started. No subagent dispatch, no parallel skill invocations, no concurrent tool calls.

## Methodology reference

The transcript-grade discipline below is borrowed from the [Tracer Bullets in LLM Planning Tickets](https://moneylion.atlassian.net/wiki/spaces/payments/pages/6684606789/Tracer+Bullets+in+LLM+Planning+Tickets) experiment, which captured every prompt, run, and tool call at full fidelity so findings remained re-derivable from raw artefacts. This demo is **not** an A/B experiment — no control prompt, n=1 per feature, no scoring of children against the tracer-bullet rubric. The borrowed convention is purely auditability: a future reader should be able to reconstruct what was asked, what was answered, and what the agent did, without re-running the demo.

## Seeds (verbatim from the user)

1. **Feature 1**: implement dual-write of encrypted account number and routing number using encryption-starter from spring-boot-starters *(user prediction: this would NOT require spike-and-report)*
2. **Feature 2**: implement cybersource repayment card transfers over network tokens by VGS guarded by feature flag *(user prediction: this WOULD require spike-and-report)*

The user's predictions are baked in as expected shapes; deviations are recorded in each feature's `Surprises / friction / bugs` section.

---

## Feature 1: encryption-starter dual-write of account + routing numbers

- **Seed (verbatim)**: implement dual-write of encrypted account number and routing number using encryption-starter from spring-boot-starters
- **Predicted repos** *(pre-run)*: `payment-platform` (data-write owner), possibly `walletapi`. `spring-boot-starters` is a library dep, typically does NOT receive a child ticket.
- **Actual repos** *(post-probe)*: `walletapi` (primary), `infrastructure` (KMS key + IAM grants). `payment-platform` was confirmed read-only consumer of bank account data; `spring-boot-starters/encryption-starter` already provides everything needed (no change).
- **Predicted shape**: 1–3 children, no Research, no spike. *Pending children-creation by `prd-to-jira-issues`.*
- **Predicted issue type for parent**: Technical Story.
- **Actual issue type for parent**: `Story` — heuristic blindspot: `walletapi` and `infrastructure` are canonical repo names but don't trigger system-actor detection because they're single lowercase words (no hyphen, no internal capital). PM accepted `Story`; rather than contort the user-stories language to game the heuristic. See friction list.
- **Predicted significance**: Inline (no Confluence elevation). **Confirmed** by `significance_check.py`.

### Q&A transcript

#### write-a-prd: graphify probe digest + scope confirmation

- **Q1** (probe digest): Does this match what you have in mind, or am I aiming wrong? Specifically interested in your read on the scope and the existing `fund_ach_information.tokenized_account_number` coexistence.
  - **A**: "yes this matches, the infrastructure is accurate as well, phase 2 is out of scope, coexist with tokenized_account_number"

#### write-a-prd: interview

- **Q2 (PM Q1, framing)**: Whose problem is the dual-write solving, in PM voice?
  - Options:
    - 1A — Compliance / security obligation (recommended).
    - 1B — New banking partner blocker.
    - 1C — Internal engineering hygiene.
    - 1D — Other / dictate.
  - **A**: 1A — Compliance / security obligation.
- **Q3 (PM Q3, user-stories voice)**: Ship the four system-voice stories drafted, or different shape?
  - Options: 3A ship as drafted (recommended) / 3B drop story 4 / 3C add a user-voice story / 3D dictate.
  - **A**: 3A — ship the drafted four system-voice stories.
- **Q4 (PM Q4, KMS key strategy)**: Dedicated key or reuse existing platform key?
  - Options: 4A dedicated `alias/walletapi-ach-pii` (recommended) / 4B reuse existing / 4C defer to research.
  - **A**: 4A — dedicated KMS key.
- **Q5 (PM Q5, rollout control)**: How to gate enabling encryption in prod?
  - Options: 5A Spring config property (recommended) / 5B LaunchDarkly flag / 5C no flag, ship direct.
  - **A**: 5C — no flag, ship `preservePlaintext=true` baked in; rollback via revert + redeploy.
- **Q6 (PM Q6, draft sign-off)**: Approve draft as-is, iterate, or PM-edit directly?
  - Options: 6A ship it / 6B iterate / 6C PM-edit.
  - **A**: 6A — ship it.

#### write-a-prd: post-script issue-type override

- **Q7 (PM Q7, parent-type after Story classification)**: Heuristic returned `Story`, contradicting your `Technical Story` prediction. How to resolve?
  - Options: 7A accept `Story` (recommended) / 7B rephrase stories to force Technical Story / 7C override at Jira UI.
  - **A**: 7A — accept `Story`. Heuristic blindspot for single-word canonical repo names noted as a follow-up plan candidate.

#### Gate A — PM review of the parent ticket

- **Q8 (PM Q8, parent edits)**: Any edits to the parent ticket (PLTPM-21272) before slicing?
  - Options: 8A no edits / 8B edit summary / 8C edit description / 8D add labels.
  - **A**: 8A — no edits.

#### prd-to-jira-issues: redline review (Gate B)

After `propose_slices.py` rendered the redline with **1 slice** (walletapi Task only — the carry-over from F1's `--primary-component` workaround propagated forward; infrastructure work was buried in the parent's `## Implementation Decisions` and not seeded as its own slice), three issues were surfaced for PM review.

- **Q9 (Issue 1, slice count)**: Only Slice A (walletapi) was seeded; KMS key + IAM grant work for `alias/walletapi-ach-pii` is described in Slice A's prose but isn't its own ticket. Shape?
  - Options:
    - 1A — Add Slice B (`infrastructure` Task) for KMS key + IAM grant + deploy-config plumbing; mark Slice A `depends_on: B` so walletapi blocks on infra. Preserves the deploy-ordering invariant from `## Further Notes` (recommended).
    - 1B — Keep 1 slice; create the infra ticket out-of-band later.
    - 1C — Drop the dedicated key entirely; reuse an existing platform key (contradicts earlier choice 4A).
  - **A**: 1A — add Slice B (infrastructure) and set Slice A `depends_on: B`.
- **Q10 (Issue 2, Slice A title)**: Auto-derived title is the entire walletapi `## Cross-Application Impact` bullet (~660 chars). Jira summary truncates around 255 chars and list views become unreadable.
  - Options:
    - 2A — Replace with `[BE] Encrypt FundDetails.bankAccountNumber + bankRoutingNumber via @Encrypted dual-write` (recommended).
    - 2B — Leave as-is; let Jira truncate.
    - 2C — Dictate a different title.
  - **A**: 2A — short title accepted as recommended.
- **Q11 (Issue 3, acceptance criteria)**: Redline ships with the literal `TODO`. `write_jira_children.py` refuses to write until ACs are filled. Which set?
  - Options:
    - 3A — Four ACs derived from the four user stories: round-trip integration test, repository lookup over plaintext-only / encrypted-only / dual-written rows, migration safety, and KMS rotation runbook (recommended).
    - 3B — Dictate.
    - 3C — Same as 3A but drop AC4 (rotation runbook owned by infra).
  - **A**: 3C — three ACs on Slice A; rotation runbook AC moved to Slice B (so Slice B ends up with four ACs of its own: KMS key provisioning, IAM key policy, walletapi config plumbing, runbook publication).

### Tool invocations


| HH:MM       | Actor        | Action                                                                                                 | Inputs (summary)                                                                                                                                             | Outputs (summary)                                                                                                                     | Duration |
| ----------- | ------------ | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| 13:34       | agent        | (probe substitute) static read of `~/payments-graph/graphify-out/GRAPH_REPORT.md` + grep               | seed: `encryption-starter dual-write of account/routing number`                                                                                              | digest: walletapi primary, infrastructure secondary, no spike needed                                                                  | ~5m      |
| 13:39       | user (PM)    | confirms scope                                                                                         | "yes this matches ... phase 2 is out of scope, coexist with tokenized_account_number"                                                                        | scope locked                                                                                                                          | ~1m      |
| 13:40–13:53 | agent ↔ user | section-by-section interview (Q2–Q6 above)                                                             | PM answers in chat                                                                                                                                           | draft `drafts/walletapi-ach-encryption-dual-write.md`                                                                                 | ~13m     |
| 13:53       | agent        | `validate_components.py` (no fixture)                                                                  | conventions path auto-walked                                                                                                                                 | exits `needs_fetch`; emits action plan asking for `getJiraProjectComponents`                                                          | <1s      |
| 13:53       | agent        | `searchJiraIssuesUsingJql`                                                                             | jql: `project = PLTPM AND component in ("walletapi", "payment-platform", "infrastructure", "spring-boot-starters")`                                          | clean parse, 0 issues — components exist (no parse error)                                                                             | <1s      |
| 13:53       | agent        | synthesise `pltpm-components-fixture.json` from the JQL inference                                      | 4 names                                                                                                                                                      | fixture written                                                                                                                       | <1s      |
| 13:53       | agent        | `validate_components.py --components-fixture ...`                                                      | the synthesised fixture                                                                                                                                      | exits `ok`                                                                                                                            | <1s      |
| 13:54       | agent        | `significance_check.py`                                                                                | the draft                                                                                                                                                    | decision: `inline` (4 stories, 2 cross-app entries, no mermaid)                                                                       | <1s      |
| 13:54       | agent        | `write_jira_prd.py` (1st pass)                                                                         | draft + decision-json                                                                                                                                        | exits `requires_user_choice` (walletapi vs infrastructure) + `Story` (not Technical Story)                                            | <1s      |
| 13:55       | agent        | edit draft Cross-App-Impact to keep only walletapi (workaround for missing `--primary-component` flag) | draft                                                                                                                                                        | draft updated                                                                                                                         | <1s      |
| 13:55       | agent        | `write_jira_prd.py` (2nd pass)                                                                         | edited draft + decision-json                                                                                                                                 | full action plan emitted, `primary_component: walletapi`                                                                              | <1s      |
| 13:56       | agent        | edit draft H1 from slug to PM-readable title; re-run `write_jira_prd.py` (3rd pass)                    | draft + decision-json                                                                                                                                        | summary now `Encrypt bank account and routing numbers at rest in walletapi (Phase 1 dual-write)`                                      | <1s      |
| 13:57       | agent        | `createJiraIssue` (1st attempt)                                                                        | projectKey=PLTPM, type=Story, components=[walletapi], summary, description (10 834 chars)                                                                    | **400 Bad Request** — `customfield_12881 Activity Type is required`                                                                   | <1s      |
| 13:57       | agent        | `getJiraProjectIssueTypesMetadata` + `getJiraIssueTypeMetaWithFields` (Story id 10001)                 | PLTPM                                                                                                                                                        | found Activity Type allowed values: New feature / Bug fix / Customer excellence / Engineering excellence / Ship & Learn / Others      | <1s      |
| 13:58       | agent        | `createJiraIssue` (2nd attempt)                                                                        | + `additional_fields.customfield_12881 = {"value": "Engineering excellence"}`                                                                                | **PLTPM-21272 created** — status `To Do`, component walletapi, AT Engineering excellence                                              | <1s      |
| 13:58       | agent        | `Delete` 5 draft/redline files (cleanup per SKILL step 10)                                             | drafts/                                                                                                                                                      | files removed                                                                                                                         | <1s      |
| 13:58       | agent        | hands off to PM for Gate A                                                                             | —                                                                                                                                                            | —                                                                                                                                     | —        |
| 14:05       | user (PM)    | Gate A response                                                                                        | "no edits"                                                                                                                                                   | scope locked                                                                                                                          | <1m      |
| 14:06       | agent        | `validate_parent.py` (no fixture)                                                                      | parent-key=PLTPM-21272                                                                                                                                       | exits `needs_fetch_parent`; emits action plan asking for `getJiraIssue`                                                               | <1s      |
| 14:06       | agent        | `getJiraIssue`                                                                                         | issueKey=PLTPM-21272, fields=summary,description,issuetype,components,status                                                                                 | parent JSON (10 765 chars description)                                                                                                | <1s      |
| 14:06       | agent        | save fixture + re-run `validate_parent.py --parent-fixture ...`                                        | the saved fixture                                                                                                                                            | normalized parent JSON to stdout (no `needs_fetch_confluence` because PRD is inline)                                                  | <1s      |
| 14:07       | agent        | `propose_slices.py --parent-fixture ...` (phase 1)                                                     | validated parent                                                                                                                                             | exits `needs_graphify`; emits 3-step plan (`graphify.search`, `get_node`, `get_edges`)                                                | <1s      |
| 14:07       | agent        | (graphify substitute) synthesise `graphify-fixture.json` from PRD + GRAPH_REPORT.md domain knowledge   | 7 modules (5 walletapi, 2 spring-boot-starters), 7 edges                                                                                                     | fixture written                                                                                                                       | ~30s     |
| 14:08       | agent        | `propose_slices.py --parent-fixture ... --graphify-fixture ...` (phase 2)                              | both fixtures                                                                                                                                                | wrote `redlines/PLTPM-21272.md`; **1 slice seeded** (Slice A walletapi Task; carry-over from F1's `--primary-component` workaround)   | <1s      |
| 14:08       | agent        | reads redline, surfaces 3 issues to PM via `AskQuestion`                                               | redline + tradeoff analysis                                                                                                                                  | structured form with 9 options across 3 issues                                                                                        | <1s      |
| 14:09       | user (PM)    | Gate B answers                                                                                         | 1A (add Slice B infrastructure) / 2A (short title) / 3C (3 ACs on A; runbook AC on B)                                                                        | —                                                                                                                                     | ~1m      |
| 14:10       | agent        | rewrite `redlines/PLTPM-21272.md` per Gate B answers                                                   | redline + PM choices                                                                                                                                         | added Slice B (infrastructure Task), set Slice A `depends_on: B`, replaced TODO with 3 ACs on A + 4 ACs on B, shortened Slice A title | <1s      |
| 14:10       | agent        | `write_jira_children.py --slice-plan ... --parent-key PLTPM-21272`                                     | the redlined slice plan                                                                                                                                      | 15-step action plan (2 createIssue + 10 createJiraSubtask + 2 createIssueLink Implement + 1 createIssueLink Blocks)                   | <1s      |
| 14:11       | agent        | `getJiraProjectIssueTypesMetadata` + `getJiraIssueTypeMetaWithFields` (Task id 3, Sub-task id 5)       | PLTPM                                                                                                                                                        | confirms Activity Type required on **both** Task and Sub-task — action plan does not include it                                       | <1s      |
| 14:11       | agent        | `createJiraIssue` (Slice A)                                                                            | type=Task, components=[walletapi], short title, 4.5k-char description, AT=Engineering excellence                                                             | **PLTPM-21278 created**                                                                                                               | <1s      |
| 14:11       | agent        | `createJiraIssue` (Slice B)                                                                            | type=Task, components=[infrastructure], short title, 3.7k-char description, AT=Engineering excellence                                                        | **PLTPM-21284 created**                                                                                                               | <1s      |
| 14:12       | agent        | `createJiraIssue` × 5 (sub-tasks of Slice A)                                                           | type=Sub-task, parent=PLTPM-21278, summaries={Development, Code Review 1, Code Review 2, Test case creation, Test case execution}, AT=Engineering excellence | **PLTPM-21290 / 21291 / 21292 / 21293 / 21294 created**                                                                               | <1s each |
| 14:12       | agent        | `createJiraIssue` × 5 (sub-tasks of Slice B)                                                           | type=Sub-task, parent=PLTPM-21284, same 5 ceremony summaries, AT=Engineering excellence                                                                      | **PLTPM-21295 / 21296 / 21297 / 21298 / 21299 created**                                                                               | <1s each |
| 14:13       | agent        | `createIssueLink` (Implement, parent→A)                                                                | type=Implement, inward=PLTPM-21272, outward=PLTPM-21278                                                                                                      | link created                                                                                                                          | <1s      |
| 14:13       | agent        | `createIssueLink` (Implement, parent→B)                                                                | type=Implement, inward=PLTPM-21272, outward=PLTPM-21284                                                                                                      | link created                                                                                                                          | <1s      |
| 14:13       | agent        | `createIssueLink` (Blocks, B→A)                                                                        | type=Blocks, inward=PLTPM-21284, outward=PLTPM-21278                                                                                                         | link created                                                                                                                          | <1s      |
| 14:13       | agent        | `Delete` × 4 (redline + 3 fixture files; cleanup per SKILL step 10)                                    | redlines/                                                                                                                                                    | files removed                                                                                                                         | <1s      |


Read-only call counts per phase: write-a-prd phase — ~12 file reads (graphify GRAPH_REPORT.md probes + payment-platform/walletapi/encryption-starter source greps), 4 Atlassian read calls (`getAccessibleAtlassianResources`, `searchJiraIssuesUsingJql`, `getJiraProjectIssueTypesMetadata`, `getJiraIssueTypeMetaWithFields`). prd-to-jira-issues phase — 1 Atlassian read (`getJiraIssue` for the parent), 2 Atlassian metadata reads (`getJiraProjectIssueTypesMetadata`, `getJiraIssueTypeMetaWithFields` × 2 for Task + Sub-task), 2 MCP descriptor reads (`createJiraIssue.json`, `createIssueLink.json` to confirm tool shapes), 1 propose_slices source read (to check fixture format).

### Artifacts

- **Parent**: [PLTPM-21272 — Encrypt bank account and routing numbers at rest in walletapi (Phase 1 dual-write)](https://moneylion.atlassian.net/browse/PLTPM-21272) — Story, component walletapi, Activity Type Engineering excellence, status To Do.
- **Children**:
  - **Slice A** [PLTPM-21278 — [BE] Encrypt FundDetails.bankAccountNumber + bankRoutingNumber via @Encrypted dual-write](https://moneylion.atlassian.net/browse/PLTPM-21278) — Task, component walletapi, AT Engineering excellence, status To Do, blocked by PLTPM-21284.
    - Sub-tasks: [PLTPM-21290 (Development)](https://moneylion.atlassian.net/browse/PLTPM-21290), [PLTPM-21291 (Code Review 1)](https://moneylion.atlassian.net/browse/PLTPM-21291), [PLTPM-21292 (Code Review 2)](https://moneylion.atlassian.net/browse/PLTPM-21292), [PLTPM-21293 (Test case creation)](https://moneylion.atlassian.net/browse/PLTPM-21293), [PLTPM-21294 (Test case execution)](https://moneylion.atlassian.net/browse/PLTPM-21294).
  - **Slice B** [PLTPM-21284 — [INFRA] Provision `alias/walletapi-ach-pii` KMS key + walletapi IAM grants](https://moneylion.atlassian.net/browse/PLTPM-21284) — Task, component infrastructure, AT Engineering excellence, status To Do, blocks PLTPM-21278.
    - Sub-tasks: [PLTPM-21295 (Development)](https://moneylion.atlassian.net/browse/PLTPM-21295), [PLTPM-21296 (Code Review 1)](https://moneylion.atlassian.net/browse/PLTPM-21296), [PLTPM-21297 (Code Review 2)](https://moneylion.atlassian.net/browse/PLTPM-21297), [PLTPM-21298 (Test case creation)](https://moneylion.atlassian.net/browse/PLTPM-21298), [PLTPM-21299 (Test case execution)](https://moneylion.atlassian.net/browse/PLTPM-21299).
- **Issue links**: parent `Implement` → A and `Implement` → B; B `Blocks` A.
- **Local draft**: deleted on cleanup per write-a-prd SKILL step 10.
- **Local redline + fixtures**: deleted on cleanup per prd-to-jira-issues SKILL step 10 (`PLTPM-21272.md`, `PLTPM-21272.parent-fixture.json`, `PLTPM-21272.validated-parent.json`, `PLTPM-21272.graphify-fixture.json`).

### Surprises / friction / bugs

*(items 1–7 captured during write-a-prd phase; items 8–12 during prd-to-jira-issues phase.)*

1. **graphify MCP not mounted in this workspace**, despite the SKILL declaring it a precondition. Static graphify-out exists at `~/payments-graph/graphify-out/` (8855 nodes, 28682 edges, 683 communities) but no MCP server wraps it. Worked around by reading `GRAPH_REPORT.md` directly.
  - *SKILL.md overstates the precondition*: `validate_components.py`, `significance_check.py`, and `write_jira_prd.py` never call graphify. The "precondition" is true only for the agent's interview anchoring (steps 1–3), not for the deterministic gates.
  - *Follow-up plan candidate*: amend SKILL.md to declare graphify "optional, falls back to static-vault read" — OR build a graphify MCP server that wraps `graph.json` so the SKILL is literally satisfied.
2. `**getJiraProjectComponents` MCP tool not exposed** on `plugin-atlassian-atlassian`. The action plan emitted by `validate_components.py` asks for it directly. Worked around by inferring component existence via JQL — a JQL with an unknown component name produces a parse error, with a known one returns ≥0 results cleanly.
  - *Follow-up plan candidate*: either teach `validate_components.py` to fall back to JQL inference automatically, OR document the JQL workaround in `validate_components.py`'s help text.
3. `**write_jira_prd.py` path bug**: `_LIB_PATH = SCRIPT_DIR.parent.parent.parent / "_lib"` resolves to `.agents/_lib` (doesn't exist) instead of `.agents/skills/_lib` (where `markdown.py` lives). One `.parent` too many because `SCRIPT_DIR = Path(__file__).parent` already captures the dir. `significance_check.py` does the same calc but starts from `Path(__file__).resolve()` (the file, not the dir), which gives it the right answer accidentally.
  - Worked around by setting `PYTHONPATH=.../skills/_lib` on every invocation.
  - *Follow-up plan*: drop one `.parent` in `write_jira_prd.py`. One-character fix; existing tests should cover it.
4. **Heuristic blindspot in `is_system_actor`**: only matches kebab-case (hyphen) or CamelCase (internal capital). The canonical repo names `walletapi` and `infrastructure` are bare lowercase single words and slip through to user-facing actor classification → forces `Story` even when all stories use canonical-repo actors.
  - PM accepted `Story` rather than contort the language.
  - *Follow-up plan candidate*: amend `is_system_actor` to also recognise the four canonical repo names verbatim (read from `jira-conventions.md`).
5. **Missing `--primary-component` flag** on `write_jira_prd.py` when `requires_user_choice` is set. Today's workaround per SKILL.md is to fix the draft (delete one of the canonical-repo bullets from `## Cross-Application Impact`). I worked around by keeping only `walletapi` and moving infrastructure to a parenthetical note + `Implementation Decisions` + `Further Notes` paragraphs. Clunky.
  - *Follow-up plan candidate*: implement the `--primary-component` flag the SKILL.md already references in a future-tense aside.
6. **PLTPM has a required `Activity Type` custom field** (`customfield_12881`) that the gru harness doesn't know about. First `createJiraIssue` failed with HTTP 400 `Activity Type is required`. Worked around by querying issue-type metadata to get allowed values, picking `Engineering excellence` for compliance-driven internal infra.
  - *Follow-up plan candidate*: extend `validate_components.py` (or a new `validate_required_fields.py`) to fetch issue-type create-screen metadata, surface project-required custom fields, and prompt the PM for them as part of the interview. Add the chosen value to the action plan.
7. **Title-as-summary friction**: `write_jira_prd.py` uses the draft H1 as the Jira summary verbatim. The slug-shaped H1 (`# walletapi-ach-encryption-dual-write`) was engineer-friendly but not PM-friendly. Required a manual H1 rewrite + script re-run. Cosmetic but adds latency.
  - *Follow-up plan candidate*: prompt the PM in the interview for a PM-readable title separate from the slug; use slug for filename, title for H1 / summary.
8. **graphify MCP gap (re-encountered, prd-to-jira-issues edition)**: `propose_slices.py`'s phase-1 action plan asks for `graphify.search`, `graphify.get_node`, `graphify.get_edges` — none of which are mounted in this workspace either. Worked around by synthesising a graphify-fixture.json (7 modules, 7 edges) from the parent PRD's named modules + GRAPH_REPORT.md community context. This was sufficient because the only structural use the script makes of graphify is weakly-connected-component splitting within a single repo bullet — and the synthesised fixture had a single connected walletapi component, so 1 slice was seeded as expected.
  - Same root cause as friction 1; same follow-up plan candidate (build a graphify MCP server or amend the SKILL to declare graphify "optional, falls back to PRD + static-vault read").
9. `**atlassian.createJiraSubtask` referenced in action plan but not exposed by the MCP**. `write_jira_children.py` emits `atlassian.createJiraSubtask` for the 10 ceremony Sub-tasks. The Atlassian MCP server only ships `createJiraIssue` (with a top-level `parent` field for sub-tasks). Worked around by translating each `createJiraSubtask` action into `createJiraIssue { issueTypeName: "Sub-task", parent: "<parent-key>", ... }`. Successful for all 10 calls.
  - *Follow-up plan candidate*: amend `write_jira_children.py` to emit `createJiraIssue` with `issueTypeName: "Sub-task"` and `parent` instead of the non-existent `createJiraSubtask` tool. One-line change in the action-plan emitter.
10. `**Activity Type` propagates to all 12 PLTPM creates, not just the parent Story** (carry-over from friction 6). Quick metadata check confirmed that PLTPM requires `customfield_12881` on **both** Task (id 3) and Sub-task (id 5) issue types. The `write_jira_children.py` action plan does not include `additional_fields.customfield_12881` for any of its 12 createIssue calls. Pre-emptively added `{ customfield_12881: { value: "Engineering excellence" } }` to all 12 calls — would have been 12 × 400 Bad Request otherwise.
  - Same root cause as friction 6, but the cost compounds: F1 ate 1 retry on the parent; without pre-emption, prd-to-jira-issues would have eaten 12 retries. The follow-up plan from friction 6 (amend `validate_components.py`/equivalent to surface required custom fields) is now noticeably higher-leverage.
11. **F1 `--primary-component` workaround propagated forward and required a manual redline edit**. Because F1's `## Cross-Application Impact` was edited down to only `walletapi` (to satisfy `write_jira_prd.py`'s `requires_user_choice`), `propose_slices.py` only seeded 1 slice — even though the deploy-ordering invariant in `## Further Notes` makes infrastructure unambiguously a separate slice with `Slice A depends_on: B` semantics. PM had to manually add `## Slice B — infrastructure: ...` to the redline + flip Slice A's `depends_on` to `B` before `write_jira_children.py` ran.
  - *Follow-up plan candidate (compound)*: implement the `--primary-component` flag on `write_jira_prd.py` (already noted in friction 5) and amend `propose_slices.py` to also seed slices from canonical-repo bullets in the parent's `## Implementation Decisions` and `## Further Notes` sections (not just `## Cross-Application Impact`), so workarounds in one don't degrade fidelity in the other.
12. **Slice title friction**: `propose_slices.py` derives Slice A's `title:` field from the entire walletapi `## Cross-Application Impact` bullet (~660 chars). Jira's summary truncates around 255 chars. PM had to dictate a short replacement ("[BE] Encrypt FundDetails.bankAccountNumber + bankRoutingNumber via @Encrypted dual-write"). Same shape as friction 7 (slug vs PM-readable H1), now repeated one level down in the slice tree.
  - *Follow-up plan candidate*: add a length cap (e.g. 200 chars) and an opinionated truncator in `propose_slices.py` that derives a slice title from the first sentence/clause of the bullet, not the whole bullet. Or prompt the PM in `write-a-prd` for a 1-line headline per cross-app bullet, separate from the prose.

*(items 13–14 surfaced post-hoc by the 2026-05-03 smoke test of the new `_lib/graphify.py` harness helper against PLTPM-21272's parent fixture; not present in the original F1 chat-time transcript.)*

13. **`propose_slices.py:_CROSS_APP_BULLET_RE` only matches `-` bullets, not `*` or `+`**. The regex `^-\s+([\w.\-/]+)\s*:\s*(.+?)\s*$` was authored against fixture drafts which all use `-`. The Atlassian ADF→markdown round-trip renders `-` bullets as `*` bullets, so any PRD that has been created via `createJiraIssue` and re-fetched via `getJiraIssue --responseContentFormat markdown` returns `* repo: ...` bullets that the parser silently drops. Result: `parse_cross_app_bullets` returns `[]`, the slicer falls through to its `if not slices` fallback (line 545), and emits a single empty-modules slice from `parent.get("primary_component")` regardless of what graphify supplied.
  - This re-frames F1's "1 slice seeded" outcome (item 11): the diagnosis at the time was "carry-over from F1's `--primary-component` workaround", but the actual proximate cause may have been this regex blindspot (the F1 demo log notes that Slice A's auto-derived title was 660 chars long, which only happens via the bullet-driven path — so either the markdown renderer's bullet-style output changed between F1 and the smoke-test refetch, or `responseContentFormat` defaulted differently in F1's call). Either way, the regex is brittle to the Atlassian round-trip in the wild.
  - *Fix shipped (commit pending)*: regex changed to `^[-*+]\s+...` (CommonMark's three valid bullet markers); added a parametrised test covering all three markers + a regression test asserting parser accepts the asterisk-bullet shape returned by the Atlassian markdown renderer.
14. **`_lib/graphify.py` `aggregate` returns mostly test-method noise against long PRD prose**. The smoke test fed PLTPM-21272's full `## Implementation Decisions` section (~3 100 chars) to `python _lib/graphify.py aggregate ...`. The helper returned 20 modules / 0 edges; **none** of the production classes named in the PRD (`EncryptedJsonAttributeConverter`, `JsonEncryptionConfig`, `EncryptedJsonAttributeConverterInitializer`, `HibernateEncryptionConfig`, `EncryptedJsonConverter`, `FundACHService`, `FundDetails`) made the top 20. Instead, the top hits are unit-test method names (`testValidateAvsCode_whenAvsCodeSkipFeatureFlagOffAndResponseCodeIsY_thenItShouldNotThrowException()`, `beforeSave with preservePlaintext keeps original field alongside encrypted field()`, etc.) that share tokens like *"encrypted" / "preservePlaintext" / "throws"* with the PRD prose.
  - Once item 13 was fixed and `parse_cross_app_bullets` correctly returned `walletapi`, the slicer dutifully consumed the helper's 9 walletapi-tagged test-method modules and — because each is a graph leaf with 0 outbound edges — split into **9 disconnected slices** (`(part 1 of 9)` through `(part 9 of 9)`), each titled with the same 660-char walletapi bullet. Garbage in, garbage out, but the wiring is intact.
  - Root cause: `_lib/graphify.py:search()` does brute-force token match against `norm_label` / `label` weighted only by token-overlap count. Long PRD prose → many tokens → many spurious matches against test-method labels (which are highly token-rich). No node-type filtering, no class-vs-test weighting, no community-importance boost.
  - *Fix shapes considered* (deferred to a separate arc — design call needed): (a) filter labels matching test-method patterns (`.test...()`, kebab-test-class paths) in `search()`'s ranker; (b) boost class-shaped labels (CamelCase, no parens, in non-`*Test*.kt` source files); (c) accept an `--anchors` flag that searches a small explicit class list parsed from the PRD's `## References` graphify-wiki bullets, instead of brute-force tokenizing the full Implementation-Decisions prose. Option (c) feels best on first read because the PRD's `## References` section already names the exact classes the PM expects — but it requires a small parser for the `graphify wiki nodes:` reference line, and a fallback if the references section is absent. Worth a dedicated arc.
  - *Smoke-test artefact*: `redlines/PLTPM-21272.smoketest.md` and `redlines/PLTPM-21272.graphify-fixture.json` (both gitignored under `redlines/`).

---

## Feature 2: Cybersource repayment card transfers over VGS network tokens

- **Seed (verbatim)**: implement cybersource repayment card transfers over network tokens by VGS guarded by feature flag
- **Predicted repos**: `payment-platform` (Cybersource adapter, VGS-NT integration), possibly `walletapi` (caller), possibly `infrastructure` (VGS vault config / Cybersource credentials).
- **Predicted shape**: 3–5 children, 1 Research ticket, spike-and-report runs.
- **Predicted issue type for parent**: Technical Story (still internal; user-facing surface unchanged).
- **Predicted significance**: Could be elevated to Confluence — VGS-as-network-token-vault plus Cybersource integration is a significant architectural piece. The skill applies the heuristic; if elevated, the parent description carries a Confluence smartlink.
- **Likely spike topic**: VGS-as-network-token-vault strategy (proxy aliasing vs. forward proxy), dual-token migration (legacy gateway tokens → VGS-issued network tokens), Cybersource API surface for NT acceptance, fallback when NT unavailable.

### Q&A transcript

#### write-a-prd: graphify probe + interview

*(to be filled in during the run)*

#### prd-to-jira-issues: redline review

*(to be filled in during the run)*

#### spike-and-report: scoping

*(to be filled in during the run)*

#### spike-and-report: report review

*(to be filled in during the run)*

### Tool invocations


| HH:MM | Actor | Action | Inputs (summary) | Outputs (summary) | Duration |
| ----- | ----- | ------ | ---------------- | ----------------- | -------- |
|       |       |        |                  |                   |          |


Read-only call counts per phase: write-a-prd *N* reads / prd-to-jira-issues *N* reads / spike-and-report *N* reads.

### Artifacts

- Parent: PLTPM-XXXXX ()
- Children: PLTPM-..., ...
- Research ticket: PLTPM-XXXXX ()
- Confluence sub-page:  (under `15 - Spikes`)
- Local draft: `drafts/<slug>.md` (deleted on cleanup)
- Local redline: `redlines/PLTPM-XXXXX.md`, `redlines/spike-PLTPM-XXXXX-report.md`

### Surprises / friction / bugs

- *(to be filled in during the run)*

---

## What we learned

*(closing section, written after both features run. Cross-feature observations on prompt-shape, redline friction, MCP failure modes; harness friction worth a follow-up plan; candidate scoring rubric for a future demo.)*