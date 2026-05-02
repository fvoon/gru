# Transfers V3 internal saga overhaul for ACH return reliability

<!-- elevate-to-confluence -->

## Problem Statement
The current TransferSaga implementation in payment-platform handles ACH returns through a brittle chain of event handlers. When Payliance pushes a return entry mid-saga, the saga occasionally double-reverses the wallet ledger, producing duplicate debit events that operations has to manually unwind.

## Solution
Refactor the saga's return-handling path to be idempotent on the (transferId, returnReasonCode) tuple. Introduce an explicit `ReturnReceived` aggregate event so reverse paths converge on a single state machine, and add an idempotency guard at the SQS consumer boundary.

## User Stories
1. As payment-platform, I want to deduplicate inbound ACH returns by (transferId, returnReasonCode), so that the saga reverses the ledger exactly once.
2. As the TransferSaga, I want to emit `ReturnReceived` as a first-class event, so that downstream listeners can subscribe to a single, named transition rather than inferring state from chained handlers.
3. As the SQS consumer, I want to reject already-processed return entries at the queue boundary, so that retries from Payliance never re-enter the saga.

## Cross-Application Impact
- payment-platform: refactor TransferSaga return-handler chain into an explicit ReturnReceived aggregate event, add idempotency guard at the SQS consumer, instrument duplicate-detection metrics, backfill saga state for in-flight transfers at deploy time.

## Implementation Decisions
- New aggregate event `ReturnReceived(transferId, returnReasonCode, providerReference)` published from the SQS consumer, consumed by TransferSaga.
- Idempotency table keyed on `(transferId, returnReasonCode)` with a unique index; duplicates are ack'd-and-dropped at the consumer.
- Backfill job replays the in-flight saga state machine for any transfer whose return arrived during the deploy window; runs once at startup behind a feature flag.
- Metrics: `saga.return.deduped` counter and `saga.return.received` counter, both tagged by reasonCode. No new dashboards in this PRD.

## Out of Scope
- walletapi changes — wallet ledger correctness is downstream of saga correctness; this PRD only changes the saga.
- Payliance contract changes — we treat their feed as the source of truth; any contract negotiation is a separate workstream.
- Replacing the existing SQS topology with EventBridge — debated but deferred; the saga refactor is independently valuable.
- Manually unwinding historical duplicate debits — operations team owns that cleanup, tracked separately.

## Further Notes
Backfill rollout will be staged behind the existing `transfer-saga-v3` Split.io flag. Coordinate the cutover with the on-call rota.

## References
- graphify wiki: TransferSaga, payment-platform-task-worker
- Narrative flows: knowledge/narrative/flows/ach-return.md
- Related ADRs: (none)
