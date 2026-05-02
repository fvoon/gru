# Jira conventions for gru

> Source of truth for Jira-side conventions. gru skills MUST read values from this file at runtime — never hardcode them. Update this file when conventions change; bump the schema version below if a change is breaking.
>
> **Schema version**: 1
> **Last verified against PLTPM**: 2026-05-02

## Site

- **Cloud ID**: `58863ddf-2ab1-4cdf-bcce-31cf76e6d270`
- **URL**: `https://moneylion.atlassian.net`
- **Jira project in scope**: `PLTPM` ("Payments")

## Bootstrap checklist (one-time, manual)

### Jira Components — 4 to create

Admin URL: <https://moneylion.atlassian.net/jira/software/c/projects/PLTPM/components>

The following 4 Components MUST exist in PLTPM. gru skills validate presence at runtime and refuse to create child tickets if any are missing:

- `payment-platform`
- `walletapi`
- `infrastructure`
- `spring-boot-starters`

**Coexistence note**: PLTPM already uses domain-shaped Components (`Payment Processor(s)`, `Wallet (Payment Methods)`, `Wallet (Transfers)`, etc.). The 4 new repo-shaped Components serve a different axis (which codebase the work touches). A single ticket may carry both — e.g., a wallet-transfers fix in walletapi gets `walletapi` AND `Wallet (Transfers)`. gru only sets the repo Component; humans / existing automation set domain Components as before.

### Workflow / link-type sanity check

These were discovered live and should already be configured (no admin work expected). Re-verify if a skill ever fails to find them:

- Status `next` (id `14423`) — used as the post-eng-review queue.
- Transition `To Do to Next` (id `221`) — `To Do` → `next`.
- Link type `Implement` (id `19455`) — outward `implements`, inward `is implemented by`.

## Issue types (PLTPM)

| Logical role          | PLTPM type name      | ID      | Notes                                              |
|-----------------------|----------------------|---------|----------------------------------------------------|
| Parent (user-facing)  | `Story`              | `10001` | Used when PRD is driven by user-facing actors      |
| Parent (internal)     | `Technical Story`    | `10707` | "Involves QA and is (mostly) back-end focussed"    |
| Child (typical)       | `Task`               | `3`     | "Anything that does not involve QA"                |
| Child (QA-involved)   | `Technical Story`    | `10707` | Same type as parent; differentiated by hierarchy   |
| Child (long research) | `Research `          | `10720` | ⚠️ TRAILING SPACE in name — use exactly            |
| Child (design work)   | `Design `            | `10747` | ⚠️ TRAILING SPACE in name — use exactly            |
| Sub-task              | `Sub-task`           | `5`     | The 5 ceremony Sub-tasks                           |

Avoid for gru-driven flow:

- `Epic` — reserved for org-level multi-feature initiatives.
- `Diagnosis` — pre-bug investigation; not part of gru's PRD-driven flow.
- `Analytics ` — analytics team only.
- `Bug` — defects; gru is feature/research-driven, not defect-driven.

### Parent type heuristic

- User-facing actor present in any user story (e.g., "As a customer / agent / merchant, ...") → `Story`.
- All actors are systems / services ("As payment-platform, ...") → `Technical Story`.
- When in doubt → `Story` (lower friction; Stories are more common).

### Child type heuristic

- Default → `Task`.
- Slice involves QA loop and is backend-focused → `Technical Story`.
- Pure research / spike → `Research ` (trailing space).
- Pure design work → `Design ` (trailing space).

## Workflow

Discovered PLTPM workflow:

```
                                                     ┌──────────────┐
                                                     │  Cancelled   │
                                                     └──────────────┘
                                                          ▲
                                                          │
To Do ──→ next ──→ In Progress ──→ In Review ──→ Ready for Test ──→ Ready for deployment ──→ Done
                                       └──→ CODE REVIEW (legacy alias of In Review)
```

### gru gate mapping

The plan calls for two gates: `eng-reviewed` (human) and `ai-ready` (AI). PLTPM has no dedicated "Ready for Eng Review" or "Ready for Development" statuses, so gru collapses both onto the existing `next` status:

| Gate            | Mechanism                                                                                          | Actor    |
|-----------------|----------------------------------------------------------------------------------------------------|----------|
| `eng-reviewed`  | Engineer transitions ticket `To Do → next` via transition id `221` ("To Do to Next").              | Human    |
| `ai-ready`      | `ai-ready-check` skill validates checklist; on pass, applies `ai-ready` **label**. Status unchanged.| AI       |

**On `ai-ready-check` failure**: skill removes any pre-existing `ai-ready` label and posts a checklist-failure comment. Status unchanged.

**Minion-pickup JQL** (reserved for stage 3):

```
project = PLTPM
  AND status = "next"
  AND labels = "ai-ready"
  AND component in ("payment-platform", "walletapi", "infrastructure", "spring-boot-starters")
  AND issuetype in ("Task", "Technical Story")
```

### Status catalogue

| Name                  | ID      | Category       |
|-----------------------|---------|----------------|
| `To Do`               | `12612` | To Do          |
| `next`                | `14423` | To Do          |
| `In Progress`         | `3`     | In Progress    |
| `In Review`           | `10004` | In Progress    |
| `CODE REVIEW`         | `11801` | In Progress    |
| `Ready for Test`      | `10300` | Done           |
| `Ready for deployment`| `12632` | In Progress    |
| `Done`                | `10001` | Done           |
| `Cancelled`           | `12000` | Done           |

### Transition catalogue (gru-relevant)

| From    | To           | Transition ID | Name             |
|---------|--------------|---------------|------------------|
| `To Do` | `next`       | `221`         | `To Do to Next`  |
| `To Do` | `Cancelled`  | `141`         | `Cancel`         |

(Other transitions exist but aren't in gru's path. Discover and add as needed.)

## Issue links

gru-relevant link types (verified live):

| gru semantic                  | Link type name           | ID      | inward              | outward      |
|-------------------------------|--------------------------|---------|---------------------|--------------|
| Parent ↔ child slice          | `Implement`              | `19455` | `is implemented by` | `implements` |
| Cross-repo dependency         | `Blocks`                 | `10000` | `is blocked by`     | `blocks`     |
| Loose relation / discovery    | `Discovery - Connected`  | `11413` | `is connected to`   | `connects to`|
| Defect created from research  | `Defect`                 | `10407` | `created by`        | `created`    |

⚠️ **Do NOT use** `Polaris work item link` (id `10409`) — it has the same `implements` semantics as `Implement`, but is reserved for Jira Polaris. gru always uses `Implement`.

### `createIssueLink` directionality

The Atlassian MCP convention: `inwardIssue` is the blocker / parent, `outwardIssue` is the blocked / child.

For gru's parent → child `Implement` link:

```
inwardIssue  = <parent-key>   # the one being implemented
outwardIssue = <child-key>    # the one doing the implementing
type         = "Implement"
```

Reads as: "child implements parent" / "parent is implemented by child".

For `is blocked by` between sibling children:

```
inwardIssue  = <blocker-key>
outwardIssue = <blocked-key>
type         = "Blocks"
```

## Ceremony Sub-tasks

When creating a child issue of type `Task` or `Technical Story`, gru auto-creates exactly these 5 Sub-tasks under it (verified against PLTPM-21031, -21032, -20920, -20921, -20922):

1. `Development`
2. `Code Review 1`
3. `Code Review 2`
4. `Test case creation`
5. `Test case execution`

Sub-tasks are created with empty descriptions and no assignee. Field defaults inherited from the parent.

Children of type `Research ` and `Design ` do NOT get ceremony Sub-tasks (no QA loop).

## Title conventions

Observed in real PLTPM tickets (PLTPM-20121, -20918, -21241):

- **Backend feature work**: prefix `[BE]`. Example: `[BE] Extend payment-platform processor-health client for merchant scope, DEGRADED, and reason codes`.
- **Multi-prefix when narrower**: `[BE][Transfer V2]`, `[BE][V3]`. Use sparingly.
- **Research tickets**: prefix `Spike:` or `Research:`. No `[BE]` — research tickets are not yet repo-scoped.
- **Design tickets**: prefix `Design:`.

Title length: aim for ≤ 100 chars. Hard cap at 255 (Jira summary limit).

## "Significant PRD" heuristic

PRD content lives **inline** in the parent Story / Technical Story description by default. Elevate to a Confluence page only if **any** of:

- The PRD contains > 10 distinct user stories.
- The PRD contains a system-architecture diagram (mermaid > 30 lines, or a Confluence-specific diagram type).
- The PRD has > 3 Cross-Application Impact entries with non-trivial detail.
- The PM explicitly tags the PRD as "elevate to Confluence" during the interview.

When elevated, the parent ticket description becomes a short summary + a Confluence smartlink to the full page. Children always reference the parent ticket key, not the Confluence page (so the Implement link remains the source of truth).

(Heuristic to be tuned after the first 2-3 real runs. Track outcomes in [`../docs/plan.md`](../docs/plan.md) → "Open setup details to resolve during bootstrap".)

## Discovered: existing PRD-driven ticket format

PLTPM already has tickets authored in gru-compatible format (e.g., PLTPM-21241). Observed sections in child descriptions:

- `## Parent PRD`
- `## What to build`
- `## Acceptance criteria` (with `[ ]` checkboxes)
- `## Out of scope`
- `## Technical notes` (optional)
- `## Blocked by`
- `## User stories addressed`

This validates gru's planned child structure. Skills should match this format exactly (engineers won't see a stylistic difference between AI-drafted and human-drafted children).
