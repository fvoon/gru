# walletapi

> **Status:** DRAFT — assembled from repo README + package layout; needs PM/eng review.
> Open questions are tagged `<!-- TODO -->`.

## Purpose

The fund-options registry and transfer hub for MoneyLion. Two responsibilities, both load-bearing for `payment-platform`:

1. **System of record for `FundOption`s** — every ACH bank account, debit/credit card, and internal house account a user owns. Each `FundOption` has one or more `ProcessorOption`s (e.g., a bank account → Payliance ACH processor option) which in turn link to one or more `ProductOption`s (loan repayments, ML+ subscription, etc.).
2. **Transfer hub** — exposes endpoints other services call to initiate transfers between any two fund options. Persists every attempted transfer event for audit. <!-- TODO: confirm whether walletapi still initiates transfers itself or if all transfer initiation now goes through payment-platform — there is an architectural overlap worth clarifying. -->

## Owners

- Eng pod: <!-- TODO — likely PLTPM (Payments) shared with payment-platform; confirm -->
- Eng lead: <!-- TODO -->
- On-call rotation: <!-- TODO -->
- Domain PM(s): <!-- TODO -->
- Authoritative docs: [Wallet API Documentation on Confluence (PS space)](https://moneylion.atlassian.net/wiki/spaces/PS/pages/942702595/Wallet+API+Documentation)

## Dominant conventions

- **Build**: Maven (single-module). Java 17 currently <!-- TODO: README still mentions Java 8 install instructions; confirm runtime version -->. `mvn clean install`. Migration script `db-migration.sh` for staged Flyway runs.
- **Test**: JUnit + Mockito (standard Spring Boot setup).
- **Persistence**: PostgreSQL via JPA. Flyway for schema migrations (`flyway_schema_history` table is the source of truth for applied migrations).
- **Secrets**: AWS Secrets Manager (requires `AWS_PROFILE=mk2acc`, `LOCAL=true` env vars when running locally).
- **Package layout** (`com.moneylion.wallet.walletapi`):
  - `api/` — REST controllers
  - `service/` — business logic
  - `repository/` — JPA repositories
  - `integration/` — outbound HTTP clients to external services
  - `event/` — inbound Kafka/SQS event handlers (banking-account events, wealth managed/active account events) <!-- TODO: confirm transport — likely Kafka given event handler/listener split -->
  - `scheduler/` — cron-style background jobs
  - `common/` — shared utilities
- **Local run**: Hits **staging** Postgres directly; no local DB setup. IntelliJ runs `WalletapiApplication` against staging by default.
- **Package publishing**: Publishes Maven packages to GitHub Packages (`moneylion-bot-infra` writes via `GITHUB_PACKAGES_WRITE_TOKEN`).

## What graphify systematically misses

- **`FundOption → ProcessorOption → ProductOption`** is a 3-level model with semantic constraints graphify can't infer (e.g., "an ACH `FundOption` typically has a Payliance `ProcessorOption`, which can be linked to a loan-repayment `ProductOption`"). The combination matrix is partially in code, partially in DB rows, partially in tribal knowledge.
- **Event listeners are wired to external topics by name** (`BankingAccountEventListener`, `WealthManagedAccountEventListener`, `WealthActiveAccountEventListener`). The producer side lives in **other services** (banking, wealth) — graphify won't show those upstream edges.
- **`payment-platform` calls `walletapi` heavily and bidirectionally** — `WalletClient` in payment-platform reads fund/processor info, updates bank info, and toggles fund availability. Graphify shows the call graph but doesn't show which calls are "hot path" vs. "rare admin operation".
- **The migration scripts shape the runtime contract.** `flyway_schema_history` evolution explains current column meanings better than the entity classes alone.

## Tribal knowledge

- **`flyway_schema_history`** is the truth for what schema is in each environment — verify there before debugging "missing column" errors.
- **Local dev points at staging Postgres.** Be careful; `LOCAL=true` does not give you an isolated DB. Don't run destructive operations from your IDE.
- **README still mentions Java 8 install steps**; the actual runtime is newer (build pulls from GitHub Packages with Java 17 starters). The README is stale on this point.
- **Two `.p12` certificate files live in the repo root** (`wfgmoneylion.p12`, `wfscmoney931.p12`) — Wells Fargo mTLS client certs for processor calls. Treat them as secrets-adjacent; rotation is manual.
- **Wallet event handlers are read-only projections** of upstream account events; they keep `walletapi`'s view of banking/wealth accounts in sync with the systems of record.
- **Loan disbursements / repayments, ML+ subscription fees, etc. are surfaced as `ProductOption`s** — adding a new MoneyLion product that needs to move money typically means adding a new `ProductOption` here, not just in `payment-platform`.
