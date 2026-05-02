---
name: ai-ready-check
description: Validates a child Jira ticket against the gru `ai-ready` contract — 6 deterministic checks (AC section present, blockers Done, exactly one canonical Component, Implement-link to a parent, status `next`, allowed issue type) — then posts a structured comment and toggles the `ai-ready` label. Re-runs are no-ops when the outcome hasn't changed. Use when an engineer has triaged a child ticket to `next` and wants the AI gate validated. Skip if the ticket isn't a `Task` / `Technical Story` (use `prd-to-jira-issues` to slice a parent first) or if the engineer hasn't yet transitioned `To Do → next`.
---

# ai-ready-check

Fourth and final skill in the gru pipeline. The dispatcher's pickup JQL is `(status = next AND labels = ai-ready)`; this skill enforces the `ai-ready` half of that contract.

## Quick start

1. **Invoke**: "use the ai-ready-check skill on PLTPM-XXXXX".
2. **Success looks like (pass)**: a structured comment with `## ai-ready-check passed` posted on the ticket and the `ai-ready` label applied. Re-runs become no-ops until the ticket changes.
3. **Success looks like (fail)**: a structured comment with `## ai-ready-check failed` listing each failed item with a concrete remediation hint, and the `ai-ready` label removed (if it was set). Re-run after fixing the items.
4. **Precondition**: Atlassian MCP, a child ticket already triaged to `next` by an engineer, and `.agents/jira-conventions.md` mentions `ai-ready` under `## Labels` (sentinel-tested).

## Workflow

6 steps. Each script invocation either emits a fetch action plan (phases 1-2) or the terminal publish-or-no-op plan (phase 3). The LLM runs the actions and feeds responses back as `--ticket-fixture` / `--blocker-fixtures`.

1. **Phase 1 — fetch the ticket**. Run `scripts/validate_ai_ready.py --ticket-key PLTPM-XXXXX`. Emits a single `atlassian.getJiraIssue` action with `expand=comments,issuelinks,renderedFields` so we can see status, components, links, and prior `ai-ready-check` comments. Save the response to a JSON file.
2. **Phase 2 — fetch blockers (if any)**. Re-run with `--ticket-fixture <path>`. If the ticket has `is blocked by` links, the script emits one `getJiraIssue` action per blocker. If there are no blockers, the script jumps to phase 3 directly.
3. **Phase 3 — terminal plan**. Re-run with `--ticket-fixture <path> --blocker-fixtures <path1> <path2> ...`. The script runs all 6 checks, parses the prior `ai-ready-check` comment (if any) via the versioned marker `<!-- ai-ready-check:v1 -->`, and emits one of:
   - `ai_ready` — all checks pass; actions: `addCommentToJiraIssue` (always) + `editJiraIssue` (label add, only if missing).
   - `not_ai_ready` — at least one check failed; actions: `addCommentToJiraIssue` (always) + `editJiraIssue` (label remove, only if currently set).
   - `no_change` — outcome matches the prior comment AND label state is correct; actions: empty. Re-runs short-circuit here.
4. **Execute the action plan**. For pass / fail, post the comment and toggle the label via the MCP tools named in the plan. The label edit uses the merge-safe REST `update.labels[{add|remove}]` shape so it won't clobber other labels.
5. **Surface the outcome**. Tell the engineer what passed / failed in one sentence; link to the ticket. On `no_change`, report "no change since the last run" and stop.
6. **Done**. There is no follow-up artifact — the comment + label are the deliverable.

## Hard rules

- **Never** add the `ai-ready` label when any check failed. The label is the AI gate; spurious labels poison the dispatcher's pickup JQL.
- **Never** transition the ticket. Status changes are owned by the engineer (`To Do → next`) or the implementation pipeline (later stages).
- **Never** edit other labels — only `ai-ready`. The merge-safe `update.labels[{add|remove}]` shape is the contract.
- **Never** judge AC quality (locked decision 2D); the check is presence-only. Quality lives at the engineer's `To Do → next` review.
- **Never** silently skip a failed check. Every failed item is named in the comment with a concrete fix.
- **Never** post a fresh comment when the outcome and label state both match the prior run. The versioned marker exists so re-runs don't spam Jira.

## Re-running

- Idempotent by design. Re-runs are no-ops while nothing has changed; once a check flips (the engineer adds AC, a blocker moves to Done, etc.), the next run posts a fresh comment + toggles the label.
- The marker is versioned (`v1`); future format bumps will increment so old comments don't false-positive.
- A re-run after a marker-format upgrade will treat the prior outcome as missing and post a fresh comment, which is the desired behavior.

## Advanced

- **Script contract, action-plan shapes, comment marker spec, edge cases**: [REFERENCE.md](REFERENCE.md).
- **Worked walkthroughs (pass + multi-fail + re-run no-change)**: [EXAMPLES.md](EXAMPLES.md).
- **Conventions** (canonical Components, allowed issue types, label policy): `.agents/jira-conventions.md`.

## Out of scope for this skill

- Auto-applying `ai-ready` based on AI judgment of ticket content. The label is the AI gate's _output_; a human applies it as a request, and `ai-ready-check` validates the contract.
- Status transitions. The engineer-reviewed `To Do → next` is the human's act.
- AC quality judgment (locked 2D).
- Webhook integration — this skill is on-demand only. A future automation wrapper can run it on every ticket update.
- JQL batch query for blocker statuses (locked rejection of 1B). Per-blocker fetches are deterministic and testable.
- In-place comment editing (locked rejection of 4C). One outcome change == one new comment.
- Editing files in product repos. gru treats the four canonical repos as read-only.
