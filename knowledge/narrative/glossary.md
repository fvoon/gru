# Cross-app glossary

> **Status**: TBA — to be authored by the `narrative-glossary` todo (see [`../../docs/plan.md`](../../docs/plan.md)).
>
> Seed entries from graphify god nodes once `~/payments-graph/` is built; curate by hand from there.

## Format

One row per term mismatch:

- **`<term-A>` (in `<repo-A>`) ↔ `<term-B>` (in `<repo-B>`)** — 1–2 sentences explaining the bridging mental model and when each is used.

## Seed candidates (to validate against graphify)

- `Transfer` (payment-platform) ↔ `Transaction` (walletapi)
- `Account` (payment-platform) ↔ `Wallet` (walletapi)
- `Event` (Axon, payment-platform) ↔ `Message` (SQS, walletapi)

(Replace with actual entries once graphify is live and `GRAPH_REPORT.md` is reviewed.)
