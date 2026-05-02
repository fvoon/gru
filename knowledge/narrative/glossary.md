# Cross-app glossary

> **Status:** DRAFT — assembled from code reading + the three drafted flows in [`flows/`](flows/); needs PM/engineer review.
> Open questions are tagged `<!-- TODO -->` inline.

Hand-curated bridge between terms that mean different things in different repos, between business vocabulary and code, or between same-named things that share no runtime code. Entries are intentionally short — if a term needs a paragraph, it belongs in [`flows/`](flows/) or [`repos/`](repos/), not here.

Authoring rule (from [`README.md`](README.md)): one bullet per term mismatch, 1–2 sentences of bridging mental model, no code dumps. **If graphify already says it, don't repeat it here.**

## Cross-repo term mismatches

- **`Transfer` (payment-platform) ↔ `Transaction` (walletapi)** — `payment-platform` orchestrates a `Transfer` (multi-step Axon saga) whose steps mutate state inside `walletapi`; `walletapi` is the system-of-record for the user's `FundOption`s and presumably exposes a per-fund record for each step's effect. One `Transfer` can therefore fan out to multiple walletapi-side records on different funds. <!-- TODO: confirm `Transaction` is the exact walletapi term for that per-fund record (could be `WalletEntry`, `Ledger`, or no first-class entry at all — verify with walletapi owners and retitle if needed). -->
- **`Account` (payment-platform) ↔ `Wallet` (walletapi)** — both refer to the user's MoneyLion deposit relationship, but `payment-platform` typically names it after its external system-of-record (the flows say "Galileo DDA" / "the user's DDA") and may not use `Account` as a first-class domain term at all, while `walletapi` owns the abstraction internally as a `Wallet` whose payment instruments are `FundOption`s. <!-- TODO: confirm whether `Account` appears in payment-platform code as a domain term or whether "DDA" is the only common name; if the latter, retitle this entry. -->
- **`Event` (Axon, payment-platform) ↔ `Message` (SQS, walletapi / infrastructure)** — both carry a "something happened" payload but their reliability stories are very different. Axon events are aggregate-scoped, transactional with the aggregate write, and persisted in the event store (so a saga can replay them); SQS messages are queue-scoped, at-least-once, with no event store and no replay. Anything that needs the event-sourcing semantics belongs on Axon; anything that needs cross-service fan-out with horizontal scale belongs on SQS.

## Code vs. business vocabulary

- **`Topup` (PM / business) ↔ `Transfer` with destination = Galileo DDA (code)** — there is no `Topup` class. PMs and engineers say "topup" to mean "external fund → user's DDA", but in code that is a `Transfer` whose destination is the user's Galileo DDA `FundOption`. Any change to "topup behavior" is therefore a change to `TransferSaga`, the processor routing service, or one of the processor task workers (see [`flows/wallet-topup.md`](flows/wallet-topup.md)).

## Intra-repo same-name-different-thing

- **`Transfer` (saga, multi-step) vs. `TransferAuthorization` (sync REST endpoint)** — both live in `payment-platform`, both prefixed "Transfer", but per ADR-0006 they share almost no runtime code. `Transfer` is an Axon aggregate driven by `TransferSaga` and runs asynchronously across multiple processor steps; `TransferAuthorization` is a plain Spring Boot endpoint that is fail-fast and synchronous, with its own aggregate and its own webhook delivery pipeline. Saga events do **not** fire on the authorization path (see [`flows/transfer-authorization.md`](flows/transfer-authorization.md)).
- **`TransferStep` ↔ `stepId` ↔ `uniqueTransactionId`** — same concept seen from three layers. `TransferStep` is the Axon entity owned by the `Transfer` aggregate; `stepId` is the public identifier on saga commands and events; `uniqueTransactionId` is the join key sent to ACH processors (Payliance, Wells Fargo) and echoed back on return entries. Anything that changes how `stepId` is generated or sent silently breaks ACH return processing — returns will land for unknown steps and be discarded (see [`flows/ach-return.md`](flows/ach-return.md) pitfall #1).

## Cross-cutting domain semantics

- **`Settled` vs. `Final` (ACH-specific)** — for ACH-funded transfers these are not synonyms. The originating step is marked `settled` (success emitted, downstream credits posted) once the ACH file is accepted by the processor, but the funds are not `final` until the ACH return window closes (~5 business days for the common cases; up to 60 days for consumer disputes). A hard ACH return inside that window flips an already-settled step into a reversal via `ConfirmTransferStepCompletion(status=ERROR)`. The `AchTransferSettledExpired` event in `TransferSaga` is how the platform models the boundary between the two (see [`flows/ach-return.md`](flows/ach-return.md)). <!-- TODO: confirm exactly which event/status transitions from "settled" to "final"; the ach-return flow names the expired event but doesn't fully document the state machine. -->
