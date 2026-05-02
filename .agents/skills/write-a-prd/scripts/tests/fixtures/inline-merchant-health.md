# Add merchant scope to processor health checks

## Problem Statement
Today, the processor-health endpoint reports a single global health value per processor. Merchants on shared processors cannot tell whether their specific configuration is degraded, so support tickets cite "everything is broken" when only one merchant's flow is affected.

## Solution
Extend the processor-health response to include a per-merchant scope. Callers can request scope via a query parameter; absence of the parameter preserves today's global behavior.

## User Stories
1. As a merchant ops user, I want to query processor health for my own merchant ID, so that I can tell whether my flows are healthy independently of other merchants on the same processor.
2. As a merchant support agent, I want a DEGRADED status with reason codes, so that I can triage incoming tickets without escalating to engineering for every flap.
3. As a merchant support agent, I want the merchant-scoped DEGRADED status visible on our internal status page, so that I can post a "we know" notice before customer tickets start arriving.

## Cross-Application Impact
- payment-platform: extend `ProcessorHealthClient` with merchant-scoped lookup, add `DEGRADED` enum + reason-code list, plumb scope through the existing health cache.

## Implementation Decisions
- New optional query parameter `merchantId` on the existing health endpoint; default behavior unchanged.
- Reason-code enum lives alongside the existing health enum; new values do not break existing consumers (additive).
- Cache key extended to `(processor, merchantId)` with TTL inherited from existing config.
- No schema changes; merchant scope is computed on-the-fly from existing per-merchant config.

## Out of Scope
- Wallet API changes — wallet does not consume processor-health directly, and none of the user stories above require error propagation through walletapi to a customer-facing surface.
- Customer-facing checkout error surfacing — out of scope here; if/when product wants this, walletapi forwarding of the new reason codes becomes its own PRD.
- Frontend display of reason codes — separate ticket once API is stable.
- Historical reason-code analytics — handled by existing observability stack.

## Further Notes
Coordinate with the merchant-support team on which reason codes are user-visible vs internal-only before merging.

## References
- graphify wiki: ProcessorHealthClient, processor-health-cache
- Narrative flows: (none directly applicable)
- Related ADRs: (none)
