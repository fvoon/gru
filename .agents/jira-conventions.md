# Jira conventions for gru

> **Status**: TBA — to be authored by the `conventions` todo (see [`docs/plan.md`](../docs/plan.md)).
>
> Skills MUST read these conventions from this file at runtime, not hardcode them. Until this file is filled in, skills should refuse to run or surface a clear "conventions not yet defined" error.

## Sections to author

1. **Workflow status names** — exact PLTPM (and other product project) status labels for "Ready for Eng Review" and "Ready for Development" equivalents. Discover via Atlassian MCP.
2. **Component-to-repo mapping** — Jira `Component` field values for `payment-platform`, `walletapi`, `infrastructure`, `spring-boot-starters`. Verify each Component exists in PLTPM (and create via admin if missing).
3. **Parent type heuristic** — when does the parent become a `Story` vs a `Technical Story`? Default rule: user-facing actors in user stories → `Story`; internal/system actors only → `Technical Story`.
4. **Child issue type heuristic** — `Task` vs `Technical Story` vs `Research` vs `Design` for child issues, and when each applies.
5. **Ceremony Sub-tasks** — the exact 5 Sub-task names auto-created under every `Task` and `Technical Story` child (Development, Code Review 1, Code Review 2, Test case creation, Test case execution — confirm exact names against PLTPM-20121).
6. **Link types** — `implements` (parent ↔ child), `is blocked by` (cross-repo dependencies), `is connected to` (loose relations), `created by` (research → spec). Verify each is enabled in PLTPM.
7. **Title conventions** — `[BE]` prefix, etc.
8. **Significant PRD heuristic** — when does a PRD elevate from inline-in-parent-description to a Confluence page?

## Bootstrap dependency

This file blocks: `skill-prd`, `skill-issues`, `skill-spike`, `skill-ready-check`. Author it before those skills.
