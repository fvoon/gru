# gru

> *"My minions, assemble!"*

`gru` is the command center for the AI sprint pipeline. It orchestrates the **idea → groomed Jira tickets** flow: PM interview, cohesion-aware PRD, per-repo ticket slicing, and an explicit `ai-ready` contract. The actual implementation loop — the **minions** that pick up `ai-ready` tickets and ship PRs — is a deferred, separable PoC. gru's outputs are designed to be the minions' inputs unchanged.

## Status

Proof-of-concept, stages 1–2 only. Active product repos in scope:

- `payment-platform`
- `walletapi`
- `infrastructure`
- `spring-boot-starters`

Stage 3 (the minions) is intentionally out of scope — see [`docs/plan.md`](docs/plan.md) for the full rationale.

## What gru does

```
PM idea ──▶ write-a-prd ──▶ parent Story / Technical Story (PRD inline)
                              │
                              ▼
                       prd-to-jira-issues ──▶ per-repo child tickets
                                                  │   (linked via "implements")
                                                  │   (5 ceremony Sub-tasks each)
                                                  │
                                                  ├─▶ Research tickets ──▶ spike-and-report
                                                  │
                                                  ▼
                                           eng review (HITL)
                                                  │
                                                  ▼
                                           ai-ready-check ──▶ ai-ready label
                                                                + status transition
                                                                = ready for minions
```

Four skills do the work, all under [`.agents/skills/`](.agents/skills):

| Skill | Purpose |
|---|---|
| `write-a-prd` | Interviews the PM, queries the cohesion KB, writes a parent Jira Story / Technical Story with an AI-optimized description. Elevates to Confluence only if the PRD is "significant". |
| `prd-to-jira-issues` | Reads the parent, queries the cohesion KB for slice boundaries, creates per-repo child tickets with the `implements` link, the right Component, the 5 ceremony Sub-tasks, and any `is blocked by` chains. Opens Research tickets for spike work. |
| `spike-and-report` | Operates on Research tickets. Runs in a throwaway worktree, grounds itself in the KB, produces a Confluence research sub-page, and links it back to the Research ticket. No PR. |
| `ai-ready-check` | Enforces the `ai-ready` checklist. On pass: applies the `ai-ready` label and transitions the ticket to "Ready for Development". On fail: removes the label and posts a comment with what's missing. |

## Cohesion knowledge base

gru does not store code. It reads cross-app cohesion via [graphify](https://github.com/safishamsi/graphify) over a merged corpus at `~/payments-graph/` (the four product repos as siblings). graphify maintains the structural graph (god nodes, edges, wiki articles, MCP server). gru contributes only the **narrative layer** that graphify can't infer:

- [`knowledge/narrative/flows/`](knowledge/narrative/flows) — mermaid sequence diagrams of critical cross-app runtime flows.
- [`knowledge/narrative/glossary.md`](knowledge/narrative/glossary.md) — cross-app semantic mismatches.
- [`knowledge/narrative/repos/`](knowledge/narrative/repos) — short README-style overview per product repo (purpose, owners, conventions, tribal knowledge).

## Prerequisites

1. **Cursor IDE** with this repo opened as the workspace.
2. **graphify** installed globally per its [install docs](https://github.com/safishamsi/graphify), and a populated `~/payments-graph/` parent directory containing the four product repos.
3. **Atlassian MCP** authenticated in Cursor (Jira + Confluence access for the relevant projects).
4. **Claude Code** or **Cursor agent** (skills work in both — symlinks at `.cursor/skills` and `.claude/skills` point to `.agents/skills`).

## Quick start

```bash
git clone https://github.com/fvoon/gru.git
cd gru

# one-time bootstrap of ~/payments-graph/ + graphify install
./scripts/setup-graphify.sh

# follow the printed next-steps to run /graphify . in Claude Code,
# then start a maintenance watcher in a background terminal:
cd ~/payments-graph && graphify --watch .

# back in the gru workspace, in Cursor IDE chat:
# "Use the write-a-prd skill, I want to add <feature>."
```

Detailed PM and engineer onboarding lives in [`docs/usage.md`](docs/usage.md) (TBA — see plan).

## Repo layout

```
gru/
├── README.md             # this file
├── AGENTS.md             # rules for working in gru itself
├── docs/
│   ├── plan.md           # the authoritative plan
│   └── usage.md          # PM + engineer onboarding (TBA)
├── .agents/
│   ├── skills/           # skill SKILL.md packages
│   └── jira-conventions.md
├── .cursor/skills  → ../.agents/skills   # symlink for Cursor IDE
├── .claude/skills  → ../.agents/skills   # symlink for Claude Code
├── knowledge/
│   └── narrative/        # hand-curated context graphify can't infer
└── scripts/              # reserved for the future minion dispatcher
```

## Why "gru"

gru gives orders. The minions execute. For now, gru only gives orders to humans (PMs, engineers) and to itself (skills calling skills, MCP-backed). When stage 3 lands, the same orders will go to long-running implementation agents that pick up `ai-ready` tickets and ship PRs. The contract between gru and its minions — the `ai-ready` ticket — is the single most important thing this repo gets right.

## Contributing

This is a working repo, not a deliverable. Treat [`docs/plan.md`](docs/plan.md) as the source of truth for *what* and *why*. Update the plan when scope or design changes; keep skills and narrative layer in sync with it.

## License

MIT — see [`LICENSE`](LICENSE).
