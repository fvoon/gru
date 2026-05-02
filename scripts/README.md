# scripts/

Reserved for the **stage 3 minion dispatcher** and any helper scripts that don't belong inside a skill.

## Currently empty by design

The PoC is intentionally scoped to stages 1–2 (idea → groomed tickets). The implementation loop — minions picking up `ai-ready` tickets and shipping PRs — is a deferred, separable PoC. When that lands, the dispatcher will live here:

- `dispatch.sh` (or `dispatch.py`) — JQL pickup, worktree create, claude-code launch, Jira transition + comment.
- Any helpers shared across multiple skills (Atlassian MCP wrappers, graphify MCP wrappers, etc.).

See [`../docs/plan.md`](../docs/plan.md) → "Path forward to stage 3 — minions" for the full plan.
