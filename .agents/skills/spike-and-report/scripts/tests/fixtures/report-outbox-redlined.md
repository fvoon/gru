# Spike report — PLTPM-21500

> **Status:** DRAFT — fill in the `TODO` placeholders below as the investigation progresses; engineer redlines before publish.

## Question

**Spike: do we need a transactional outbox for wallet-to-wallet events?**

Investigate whether the new wallet-to-wallet event publisher requires a transactional outbox or whether the existing Axon event store guarantees suffice. Cover failure-mode analysis (publisher down, consumer down, network partition) and recommend an approach with a high-level migration path.

## Existing state (from graphify)

- `WalletEventPublisher` (payment-platform) writes to `WalletEventStore` and publishes to `WalletEventConsumer` in walletapi.
- Today the publish + persist happen in the same Axon `UnitOfWork` — committed atomically against the Axon event store.
- `WalletEventConsumer` is the only known consumer; no fan-out yet.

## What was tried

- **Approach A — rely on Axon event store guarantees only.** The existing `WalletEventPublisher.publish` path commits writes atomically inside the Axon `UnitOfWork`. Verified by reading commit `abc1234` on the spike branch.

- **Approach B — introduce a `wallet_outbox` table with a poller publishing to SQS.** Spiked a minimal table + poller in `payment-platform` (commit `def5678` on spike branch). Confirmed under simulated SQS outage that the poller backs off cleanly and the outbox table grows linearly.

  ```java
  public void publish(WalletEvent e) {
      eventStore.publish(e);
      outboxRepository.enqueue(e);
  }
  ```

- **Approach C — keep Axon, add idempotent retries on the consumer.** Quick read of `WalletEventConsumer` shows it already deduplicates by event id. So this is a superset of Approach A.

## What worked / what didn't

- Approach A is the simplest, but it couples publisher availability to consumer availability — observed during the simulated SQS outage that publisher backpressure built up after ~30 seconds.
- Approach B's outbox table is the only option that fully decouples the two systems. It costs one row write per event but the row is small (event id + payload reference, not the payload itself).
- Approach C alone is insufficient — without an outbox, retries don't help when the publisher itself can't reach the broker.

## Recommended approach

**Approach B — a transactional outbox is worth the row-write cost** because the cross-region SQS retry profile we observed in `infrastructure/sqs.yaml` already has a P99 of 4.2s, which is enough to cause publisher-side backpressure under load. The outbox table makes the publish path purely local and durable; the poller absorbs the broker variability asynchronously.

### Recommended contract changes

- Add `wallet_outbox` table with columns `(id, payload_ref, created_at, published_at, attempt_count)`.
- New SQS topic `wallet-events-outbox` (separate from the existing `wallet-events` topic so we can drain the outbox cleanly during cutover).
- Consumer ack contract unchanged.

## Suggested follow-up tickets

- `[BE] Add wallet_outbox table` — schema migration only, no functional change.
- `[BE] Wire poller to SQS for wallet_outbox` — the actual outbox publisher; gated behind a feature flag for the cutover.
- `[BE] Cutover plan for wallet-events -> wallet-events-outbox` — a runbook + flag rollout, not code.
- `[Ops] Dashboards for outbox lag + attempt-count distribution` — observability for the new table.

## Spike metadata

_(auto-populated by write_spike_report.py at publish time.)_
