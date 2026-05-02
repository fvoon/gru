# payment-platform

> **Status:** DRAFT — assembled from repo READMEs + ADRs + code reading; needs PM/eng review.
> Open questions are tagged `<!-- TODO -->`.

## Purpose

The transfer orchestrator for MoneyLion. Owns the lifecycle of every money movement that crosses a fund option — pulls from one source (card, ACH, wallet), pushes to one destination (card, ACH, DDA, internal house account), and tracks every step. Multi-tenant across products: `IC`, `LOAN`, `MEMBERSHIP`, etc. (see `Product` enum). Multi-channel across payment methods: `DEBIT_CARD`, `ACH`, `CRYPTO`.

## Owners

- Eng pod: **PLTPM** (Payments) — matches the Jira project key.
- Eng lead: <!-- TODO -->
- On-call rotation: <!-- TODO — link PagerDuty schedule -->
- Domain PM(s): <!-- TODO -->

## Dominant conventions

- **Build**: Maven, multi-module. Top-level `pom.xml` aggregates `transfer/`, `common/`, and many `*-integration/` submodules — one per external processor (Payliance, Wells Fargo, Checkout.com, Cybersource, Galileo DDA, Tabapay, VGS, NACHA, etc.).
- **Build profile**: `mvn clean package -P transfer -T 1.5C`. Profiles are load-bearing — `-P transfer` is the canonical build.
- **Test**: JUnit + Mockito, conventional layout under `src/test/java`. Hoverfly profile (`SPRING_PROFILES_ACTIVE=hoverfly`) for API simulation against external processors.
- **Error handling**: **Vavr `Either<ResultCode, T>`** is the project standard (ADR-0001). Exceptions are reserved for unrecoverable / programmer errors.
- **Persistence**: Aurora Postgres via **IAM authentication** — no static DB credentials in secrets. Flyway runs migrations both manually (legacy) and on app boot.
- **Messaging**:
  - **Axon Framework** for CQRS / event sourcing for the long-running `Transfer` aggregate (saga: `TransferSaga`).
  - **SQS** for task-worker fan-out (one queue per integration step, e.g. `payliance-return`, `wells-fargo-return`, Galileo DDA request/response, Checkout capture/void).
  - Spring profiles separate API runtime from worker runtime: `api`, `instantTasks`, `delayTasks`, `dlqTasks`.
- **Feature flags**: Split.io via `SplitFeature` constants. Processor routing decisions are commonly feature-flagged per user / treatment.
- **DB conn auth**: IAM (no usernames/passwords in env or secrets).
- **ADRs**: `docs/adr/` — read these before proposing architectural changes. Particularly relevant: 0001 (Vavr Either), 0002 (transfer orchestration), 0006 (sync auth endpoint), 0007 (webhook events).

## What graphify systematically misses

- **Axon command/event wiring is name-based** — `@CommandHandler` and `@SagaEventHandler` annotations dispatch by class type, not by explicit edges in code. Graphify will see classes but won't always reconstruct the full saga state machine. For lifecycle questions, read `TransferSaga.java` directly.
- **Two parallel "transfer" runtimes coexist** — the multi-step `TransferSaga` (Axon, async) and the synchronous `TransferAuthorizationService` REST endpoint (per ADR-0006). They share fund/processor lookup code via `WalletClient` and `ProcessorRoutingSelectionService` but have different aggregates, different failure semantics, and different webhook paths. Graphify edges may suggest more shared logic than actually exists at runtime.
- **Processor selection is feature-flagged.** `ProcessorRoutingSelectionService` reads Split treatments at runtime; the same input can route to different processors per user / environment. Static analysis can't show this.
- **SQS queue ↔ task-worker mapping lives in config**, not in code. Each `*Task` class has a SQS URL injected via Spring; graphify won't link the worker to the queue without the config files.
- **`infrastructure` provisions every queue, DB, secret, and IAM role this service uses.** Cross-repo references are by name, not import.

## Tribal knowledge

- **`moneylion` is a magic user id** for house-account legs of a transfer (e.g., the merchant side of a card disbursement). Tooling that filters by user must mirror `PaylianceReturnProcessTask`'s logic of skipping `"moneylion"` when deriving the real user id.
- **Idempotency is per-`stepId`, not per-request.** SQS retries dedupe by `stepId` / `uniqueTransactionId`. A caller retrying `POST /transfers` creates a new aggregate. <!-- TODO: confirm whether caller-side idempotency keys are honored -->
- **ACH transfers are "settled-pending-return"** until the ACH window passes (~5 business days). The saga emits `AchTransferSettledExpired` separately from the return-driven failure path; treat ACH success as provisional until both signals agree.
- **Hard return codes are hardcoded** in `AchHardReturnCodes.HARD_RETURN_CODES` — adding/removing a code requires a code change + redeploy.
- **There is no `Topup` class.** "Wallet topup" is a `Transfer` whose destination is a Galileo DDA fund option. Anyone hunting for topup behavior should grep on the destination `FundOption` type, not on a class name.
- **The `transfer/README.md` is the canonical README** — top-level `README.md` just redirects there.
