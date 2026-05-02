# Per-repo narrative overviews

One short README-style file per product repo in scope. Captures what graphify systematically misses.

## Scope

- `payment-platform.md` — DRAFT, pending PM/eng review
- `walletapi.md` — DRAFT, pending PM/eng review
- `infrastructure.md` — DRAFT, pending PM/eng review
- `spring-boot-starters.md` — DRAFT, pending PM/eng review

Each draft has `<!-- TODO -->` markers where the assumption was made from
second-hand code reading and needs human confirmation. Owners + on-call
rotations are the most common gap.

## Format per file

```markdown
# <repo-name>

## Purpose
1–2 sentences. What this repo is for in the platform.

## Owners
- Eng lead: <name / handle>
- Domain owner / PM: <name>
- On-call rotation: <link>

## Dominant conventions
- Build: <Maven / Gradle / etc.>
- Test: <JUnit / etc.>
- Error handling: <Vavr Either / exceptions / etc.>
- Persistence: <Postgres / DynamoDB / etc.>
- Messaging: <Axon / SQS / Kafka / etc.>

## What graphify systematically misses
- <e.g., Axon event wiring inferred from string constants — review `payment-platform/src/main/java/.../events/` manually>
- <e.g., infrastructure-as-code references to other repos via tag selectors>

## Tribal knowledge
- <gotcha 1>
- <historical decision worth remembering 1>
```

(Author each file under the `narrative-repos` todo in [`../../docs/plan.md`](../../docs/plan.md).)
