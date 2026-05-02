# Narrative knowledge layer

Hand-curated context that complements [graphify](https://github.com/safishamsi/graphify). graphify maintains the **structural** graph (god nodes, edges, wiki articles) over the merged corpus at `~/payments-graph/`. This directory holds the **narrative** — the why, the cross-app event chains, the semantic mismatches, and the tribal knowledge that graphify can't infer.

## What lives here

```
narrative/
├── README.md                         # this file
├── flows/                            # cross-app runtime sequences
│   ├── wallet-topup.md
│   ├── transfer-authorization.md
│   └── ach-return.md
├── glossary.md                       # cross-app semantic mismatches
└── repos/                            # short README-style repo overviews
    ├── payment-platform.md
    ├── walletapi.md
    ├── infrastructure.md
    └── spring-boot-starters.md
```

## Authoring rules

- **If graphify already says it, don't repeat it here.** Narrative complements; it does not duplicate.
- **`flows/<flow>.md`** — one mermaid sequence diagram + 1–3 paragraphs of when/why context. No code dumps. Pick flows that cross at least 2 product repos and that PMs/engineers actually reference in conversation.
- **`glossary.md`** — bullet entries, each one term-mismatch (e.g., "`Transfer` in payment-platform vs. `Transaction` in walletapi"), with the bridging mental model in 1–2 sentences.
- **`repos/<name>.md`** — short overview per product repo: purpose, owners, dominant conventions, what graphify systematically misses (e.g., Axon event wiring inferred from string constants).

## Maintenance

This layer drifts faster than code. Refresh cadence:

- **`flows/`** — when a runtime flow changes meaningfully (new event, new participant, new failure mode).
- **`glossary.md`** — when a PR adds a new domain term or renames an existing one.
- **`repos/`** — when ownership changes, when a major convention shifts, or when a `GRAPH_REPORT.md` review surfaces a systematic gap.

A skill or PR that surfaces narrative drift should open a `gru` issue rather than silently fix it — drift detection is a feature.

## Graphify install + maintenance ritual

### Install (one-time)

Use the bootstrap script:

```bash
./scripts/setup-graphify.sh
```

It creates `~/payments-graph/`, symlinks the 4 product repos as siblings, and installs graphify (`pip install graphifyy && graphify install`). See [`../../scripts/README.md`](../../scripts/README.md).

After bootstrap, in Claude Code from `~/payments-graph/`:

```
/graphify . --wiki --mcp
```

`--wiki` builds agent-crawlable articles under `graphify-out/wiki/`. `--mcp` starts the MCP stdio server that gru skills query directly.

### Maintenance ritual

The merged corpus must stay current for the cohesion KB to be useful. The chosen ritual is **demo-time `--watch`, ad-hoc `graphify update` otherwise**. Per-repo git hooks were considered and rejected (see "Why not per-repo hooks" below).

#### Demo / active-session use → `graphify --watch`

When you're about to demo gru, run multiple agents in parallel, or otherwise expect the corpus to change rapidly during the session:

```bash
cd ~/payments-graph
graphify --watch .
```

The watcher is a **foreground process** in that terminal — leave it running, switch tabs, do your work; it rebuilds the AST graph on every file save. **Stop it deliberately when you're done** — see "Stopping / disabling" below. We do NOT run this as an always-on daemon; the cost (background CPU, surprise rebuilds, divergent graph state across machines) is not worth it outside an active session.

Note: `--watch` rebuilds AST-only on file changes. Doc / image changes are noticed but not LLM-rebuilt — run `graphify update .` to pick those up.

#### Quiescent / non-demo use → `graphify update` ad-hoc

The rest of the time, leave the graph as-is. Before invoking a gru skill that would benefit from a fresh graph, run a one-shot update:

```bash
cd ~/payments-graph && graphify update .
```

This is no-LLM (fast), idempotent, and writes to the same `~/payments-graph/graphify-out/` the MCP server queries.

#### Stopping / disabling

The watcher has no daemon supervisor; you stop the process directly.

| Started as | Stop with |
|---|---|
| `graphify --watch .` in a terminal (foreground) | `Ctrl-C` in that terminal |
| Backgrounded with `&` | `pkill -f 'graphify .* watch'` (or `kill <pid>` if you tracked it) |

If anyone in the team installed per-repo git hooks before reading this section (or migrated from another setup), uninstall them per repo:

```bash
for r in payment-platform walletapi infrastructure spring-boot-starters; do
  (cd ~/IdeaProjects/$r && graphify hook uninstall)
done
```

#### Why not per-repo hooks

`graphify hook install` writes a `post-commit` hook that calls `_rebuild_code(Path('.'))` from `[graphify/hooks.py](https://github.com/safishamsi/graphify/blob/main/graphify/hooks.py)` (lines 64-82 in v0.6.2). `Path('.')` resolves to the **per-repo** working directory at hook execution time — not `~/payments-graph/`. The hook would rebuild `~/IdeaProjects/<repo>/graphify-out/` (per-repo graph), leaving `~/payments-graph/graphify-out/` (merged corpus, what gru's MCP queries) stale.

Source-level inspection was sufficient verification; no commit-cycle test was needed. If graphify ever gains a `--corpus-dir` flag for hooks, revisit this decision.
