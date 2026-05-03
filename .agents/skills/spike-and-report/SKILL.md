---
name: spike-and-report
description: Bootstraps a throwaway worktree for a Research ticket, runs a budgeted investigation against the four canonical repos, and publishes a structured Confluence sub-page report linked back to the Research ticket. Use when a Research-typed Jira issue has been triaged and an engineer wants the agent to spike + report rather than open a PR. Skip if the work is implementation-shaped (use `prd-to-jira-issues` instead) or if no Research ticket exists yet.
---

# spike-and-report

Third skill in the gru pipeline. Picks up a `Research`-typed Jira ticket, creates a disposable git worktree per affected repo, runs a budgeted investigation, and ships the findings as a 7-section Confluence sub-page — linked back to the Research ticket and transitioned to Done.

## Quick start

1. **Invoke**: "use the spike-and-report skill on PLTPM-XXXXX".
2. **Success looks like**: a Confluence page URL posted in chat, the same URL commented on the Research ticket, and the ticket transitioned to Done. Worktree(s) are left in place; cleanup commands are in `SPIKE_README.md` inside each.
3. **Precondition**: Atlassian MCP and a `Research`-typed Jira ticket already exist. `.agents/jira-conventions.md` must have the `## Confluence` section bootstrapped (real space id + Spikes parent page id, not `<TBA — bootstrap>`). Graphify queries are served by the `_lib/graphify.py` harness helper — see "Harness helpers" in gru's `AGENTS.md`. No standalone graphify MCP is required.

## Workflow

10 steps. Pause for the engineer only at step 8 (review checkpoint) and on validation errors.

1. **Validate target**. Run `scripts/validate_spike_target.py --research-key PLTPM-XXXXX`. It checks conventions, refuses if a redline / worktree / Done-status / existing Confluence link signals a prior run, and emits a phase-1 action plan to fetch the Research ticket.
2. **Fetch Research ticket**. Call `atlassian.getJiraIssue` per the plan; save the response; re-run `validate_spike_target.py --research-fixture <path>`.
3. **Fetch parent (if any)**. If step 2 exits with `needs_fetch_parent`, call `atlassian.getJiraIssue` for the parent key and re-run with `--parent-fixture`. If the parent is elevated, the script emits a `needs_fetch_confluence` phase next.
4. **Fetch Confluence parent (if elevated)**. Call `atlassian.getConfluencePage` for the elevated parent's page id; re-run with `--confluence-fixture`. The 3-level walk (elevated PRD → configured Spikes parent → orphan Spikes parent) decides where the report's Confluence sub-page lands.
5. **Repo selection + graphify queries**. Pass `--repos` to scope the spike to a subset of the four canonical product repos. The script emits graphify queries scoped to those repos.
6. **Aggregate graphify**. Run the queries; aggregate the responses into `{nodes, edges, communities}`; save as a fixture; re-run with `--graphify-fixture`. The script's final stdout is the normalized target JSON — pipe it to a file.
7. **Bootstrap worktree(s)**. Run `scripts/bootstrap_worktree.py --target-fixture <validated-target.json>`. Executes the action plan: `git worktree add` per repo, `git config push.default nothing`, writes `.spike-state.json`, per-worktree `SPIKE_README.md`, `redlines/spike-<KEY>-briefing.md`, and `redlines/spike-<KEY>-report.md` (skeleton).
8. **Investigation + review checkpoint**. Read the briefing. Investigate inside the worktree(s). Append findings to `redlines/spike-<KEY>-report.md`, filling the `TODO` placeholders. **Hard rules**: never `git push`, never `gh pr create`, no PR is opened from this branch. Trial commits on `spike/<KEY>` are fine. Hand the redlined report to the engineer for review; engineer edits prose + clears `TODO`s.
9. **Publish**. Run `scripts/write_spike_report.py --report redlines/spike-<KEY>-report.md`. Emits a 3-phase action plan: createConfluencePage (under the resolved parent), addCommentToJiraIssue (with the page URL), transitionJiraIssue (to Done).
10. **Cleanup**. Worktrees stay in place by design; the engineer or a follow-up session removes them. The action plan's comment body links to `SPIKE_README.md` for the cleanup command. Delete `redlines/spike-<KEY>-*.md` after the report ships (the redlines directory is gitignored).

## Hard rules during the spike

- **No PR is ever opened from a spike branch.** `push.default=nothing` is set in each worktree as a belt+braces second line of defence; the agent's first line is "do not run `git push` or `gh pr create`".
- **Wall-clock budget**: ~30 minutes (soft). The metadata footer in the report stamps actual elapsed and flags `BUDGET_EXCEEDED` if the spike ran long.
- **Turn budget**: ~30 agent turns (LLM-self-policed). Pivot to writing the report by turn ~25 if you haven't already.
- **No spec edits**. gru treats the four canonical product repos as read-only; the worktree is for trial commits only.
- **No arbitrary Confluence content**. The 7-section template is the publish format; anything outside it gets escaped to literal text.

## Re-running

- A clean re-run requires removing the redline + the worktree + (if a prior page was published) the Confluence URL from the Research ticket comments.
- For a controlled overwrite of an existing Confluence page (rare; e.g. the engineer wants to revise a published spike), pass `--update-existing --existing-page-id <pageId>` to `write_spike_report.py`.
- For a fresh worktree on the same Research ticket (e.g. the prior worktree was corrupted), pass `--reset-worktree` to `bootstrap_worktree.py`.

## Advanced

- **Script contracts, action-plan shapes, MCP call patterns, `--update-existing` semantics**: [REFERENCE.md](REFERENCE.md).
- **Worked walkthroughs (elevated parent + orphan)**: [EXAMPLES.md](EXAMPLES.md).
- **Conventions** (Research issue type, Confluence parents, schema_version): `.agents/jira-conventions.md`.

## Out of scope for this skill

- Drafting the parent PRD — that's `write-a-prd`.
- Slicing a parent into per-repo Tasks — that's `prd-to-jira-issues`.
- Auto-generating follow-up implementation tickets from spike findings (the report's "Suggested follow-up tickets" section is a hand-paste source for a follow-up PRD).
- Auto-pushing the spike branch / opening a PR.
- Pre-push git hooks (`push.default=nothing` is the chosen guardrail).
- Multi-attempt spikes that share state across sessions (re-runs are explicit + manual).
- Editing files in product repos. The worktree is the only place trial commits happen.
