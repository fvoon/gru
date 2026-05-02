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

## Graphify maintenance ritual

The merged corpus at `~/payments-graph/` must stay current for the cohesion KB to be useful. Choose one:

- **`graphify --watch ~/payments-graph/`** in a background terminal during active gru work (simple, ephemeral).
- **Per-repo post-commit hooks** (`graphify hook install` per product repo) so any commit anywhere triggers a rebuild (durable, but verify it cooperates with the merged-corpus layout).

Document the chosen approach here once decided (`graphify-maintenance` todo in `docs/plan.md`).
