# Narrative flows

Cross-app runtime sequence diagrams. One file per flow.

## When to add a flow

- The flow crosses **at least 2 product repos**.
- PMs or engineers reference it in conversation; it's load-bearing for a feature class.
- graphify's structural edges alone don't make the runtime ordering, retries, or failure semantics obvious.

## Format per file

```markdown
# <Flow name>

## Participants
- <repo / service> — <role in this flow>
- ...

## Sequence

\`\`\`mermaid
sequenceDiagram
    participant A as <repo-A>
    participant B as <repo-B>
    A->>B: <event / call>
    B-->>A: <response / ack>
\`\`\`

## When this fires
1–3 paragraphs of context: trigger, success path, primary failure modes, idempotency assumptions, retries.

## Pitfalls
- <gotcha 1>
- <gotcha 2>
```

## Initial set

- `wallet-topup.md` — DRAFT, pending PM/eng review
- `transfer-authorization.md` — DRAFT, pending PM/eng review
- `ach-return.md` — DRAFT, pending PM/eng review

Each draft has `<!-- TODO -->` markers where the assumption was made from second-hand
code reading and needs human confirmation.

(Pick the rest with PMs after the first three are reviewed.)
