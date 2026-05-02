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

The merged corpus must stay current for the cohesion KB to be useful. Pick one (and document the choice below):

| Option | Trade-off | When to choose |
|---|---|---|
| **`graphify --watch ~/payments-graph/`** in a background terminal | Simple, ephemeral, instant rebuild on file save (AST only). Doc/image changes notify but don't auto-LLM-rebuild — run `--update` for those. | During active gru sessions where multiple agents are writing in parallel. |
| **Per-repo post-commit hooks** (`graphify hook install` in each product repo) | Durable, runs once per commit, no background process. Open question: does it cooperate with the merged-corpus layout? | When gru is used intermittently and a stale graph would be a footgun. |

**Open question (from [`../../docs/plan.md`](../../docs/plan.md))**: per-repo `graphify hook install` was designed for single-corpus layouts. Verify it correctly drives the merged-corpus rebuild before defaulting to it. If verification fails, default to `--watch`.

**Chosen approach for our team**: TBA — fill in once decided.
