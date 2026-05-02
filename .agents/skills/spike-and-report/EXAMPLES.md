# spike-and-report — Worked examples

Two end-to-end walkthroughs covering the elevated-parent (canonical) and orphan (no-parent) flows. Both mirror the test goldens; use these as a sanity check or as a recipe for explaining the skill to a teammate.

## Example 1: elevated parent → multi-repo spike

**Setup**

- Research ticket: `PLTPM-21500` "Spike: do we need a transactional outbox for wallet-to-wallet events?".
- Parent: `PLTPM-21000` "Wallet-to-wallet event integration" (Story, elevated — has a Confluence page).
- Repos in scope: `payment-platform`, `walletapi`.
- Expected output: a Confluence sub-page under the elevated parent's PRD page, comment on the Research ticket, ticket transitioned to Done.

The corresponding fixtures live in [`scripts/tests/fixtures/`](scripts/tests/fixtures/):

- [`research-elevated-parent.json`](scripts/tests/fixtures/research-elevated-parent.json) — `validate_spike_target.py`'s terminal `ok` payload.
- [`graphify-spike-outbox.json`](scripts/tests/fixtures/graphify-spike-outbox.json) — graphify response embedded into the target.
- [`confluence-elevated-parent-page.json`](scripts/tests/fixtures/confluence-elevated-parent-page.json) — the parent PRD's Confluence body.
- [`state-outbox.json`](scripts/tests/fixtures/state-outbox.json) — `.spike-state.json` written by `bootstrap_worktree.py`.
- [`report-outbox-redlined.md`](scripts/tests/fixtures/report-outbox-redlined.md) — the engineer-redlined 7-section report.
- [`briefing-outbox.expected.md`](scripts/tests/fixtures/briefing-outbox.expected.md) — golden: briefing emitted by `bootstrap_worktree.py`.
- [`outbox-write-action-plan.expected.json`](scripts/tests/fixtures/outbox-write-action-plan.expected.json) — golden: full publish action plan.

### Run-through

**Step 1: Validate target**

```
$ python scripts/validate_spike_target.py --research-key PLTPM-21500
{
  "status": "needs_fetch_research",
  "actions": [{"step": 1, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-21500"}}]
}
```

The agent calls `atlassian.getJiraIssue(issueKey="PLTPM-21500")`, saves the response to `research.json`, and re-runs:

```
$ python scripts/validate_spike_target.py --research-key PLTPM-21500 \
    --research-fixture research.json
{
  "status": "needs_fetch_parent",
  "parent_key": "PLTPM-21000",
  "actions": [{"step": 1, "tool": "atlassian.getJiraIssue", "args": {"issueKey": "PLTPM-21000"}}]
}
```

The agent fetches the parent ticket, saves to `parent.json`, re-runs:

```
$ python scripts/validate_spike_target.py --research-key PLTPM-21500 \
    --research-fixture research.json --parent-fixture parent.json
{
  "status": "needs_fetch_confluence",
  "confluence_url": "https://example.atlassian.net/wiki/spaces/PLTPM/pages/3333",
  "actions": [{"step": 1, "tool": "atlassian.getConfluencePage", "args": {"pageId": "3333"}}]
}
```

The agent fetches the Confluence page, saves to `confluence.json`, re-runs:

```
$ python scripts/validate_spike_target.py --research-key PLTPM-21500 \
    --research-fixture research.json --parent-fixture parent.json \
    --confluence-fixture confluence.json
{
  "status": "needs_repo_selection",
  "candidate_repos": ["payment-platform", "walletapi"],
  "next_step": "Re-run with --repos payment-platform,walletapi"
}
```

The engineer (or agent, prompted) chooses the repos:

```
$ python scripts/validate_spike_target.py --research-key PLTPM-21500 \
    --research-fixture research.json --parent-fixture parent.json \
    --confluence-fixture confluence.json --repos payment-platform,walletapi
{
  "status": "needs_graphify",
  "actions": [
    {"step": 1, "tool": "graphify.search", "args": {"text": "transactional outbox wallet events", "scope": {"repos": ["payment-platform", "walletapi"]}}},
    {"step": 2, "tool": "graphify.get_node", "args": {"id": "<PER_HIT>"}},
    {"step": 3, "tool": "graphify.get_edges", "args": {"from_id": "<EACH_HIT>"}}
  ]
}
```

The agent aggregates graphify responses into `graphify.json` (matching `graphify-spike-outbox.json`'s shape), then runs the final pass:

```
$ python scripts/validate_spike_target.py --research-key PLTPM-21500 \
    --research-fixture research.json --parent-fixture parent.json \
    --confluence-fixture confluence.json --repos payment-platform,walletapi \
    --graphify-fixture graphify.json > target.json
```

`target.json` is the terminal `ok` payload (matching `research-elevated-parent.json`'s shape).

**Step 2: Bootstrap worktree(s)**

```
$ python scripts/bootstrap_worktree.py --target-fixture target.json
{
  "status": "ready",
  "research_key": "PLTPM-21500",
  "branch": "spike/PLTPM-21500",
  "worktree_root": "/Users/<you>/IdeaProjects/spike/PLTPM-21500",
  "redline_dir": "/Users/<you>/IdeaProjects/gru/.agents/skills/spike-and-report/redlines",
  "actions": [
    {"step": 1, "type": "shell", "command": ["git", "-C", "/Users/<you>/IdeaProjects/payment-platform", "worktree", "add", "-B", "spike/PLTPM-21500", "/Users/<you>/IdeaProjects/spike/PLTPM-21500/payment-platform"]},
    {"step": 2, "type": "shell", "command": ["git", "-C", "/Users/<you>/IdeaProjects/spike/PLTPM-21500/payment-platform", "config", "--local", "push.default", "nothing"]},
    {"step": 3, "type": "shell", "command": ["git", "-C", "/Users/<you>/IdeaProjects/walletapi", "worktree", "add", "-B", "spike/PLTPM-21500", "/Users/<you>/IdeaProjects/spike/PLTPM-21500/walletapi"]},
    {"step": 4, "type": "shell", "command": ["git", "-C", "...", "config", "--local", "push.default", "nothing"]},
    {"step": 5, "type": "write_file", "path": ".../.spike-state.json", "contents": "{...}"},
    {"step": 6, "type": "write_file", "path": ".../payment-platform/SPIKE_README.md", "contents": "# spike worktree..."},
    {"step": 7, "type": "write_file", "path": ".../walletapi/SPIKE_README.md", "contents": "# spike worktree..."},
    {"step": 8, "type": "write_file", "path": "redlines/spike-PLTPM-21500-briefing.md", "contents": "# Spike briefing — ..."},
    {"step": 9, "type": "write_file", "path": "redlines/spike-PLTPM-21500-report.md", "contents": "# Spike report — ..."}
  ]
}
```

The agent executes each action — 4 shell + 5 write_file. Result:
- Two worktrees: `~/IdeaProjects/spike/PLTPM-21500/{payment-platform,walletapi}/`, both on `spike/PLTPM-21500`, both with `push.default=nothing`.
- `~/IdeaProjects/spike/PLTPM-21500/.spike-state.json` carrying budget + parent + worktree paths.
- `redlines/spike-PLTPM-21500-briefing.md` matching [`briefing-outbox.expected.md`](scripts/tests/fixtures/briefing-outbox.expected.md).
- `redlines/spike-PLTPM-21500-report.md` (skeleton; the agent appends findings to this).

**Step 3: Investigation + redline review**

The agent reads the briefing, opens the worktree(s), tries each approach (committing to `spike/PLTPM-21500` along the way), and fills in the `TODO` placeholders in `redlines/spike-PLTPM-21500-report.md`. Once the agent thinks it's done, the engineer reviews and edits — the redline ends up matching [`report-outbox-redlined.md`](scripts/tests/fixtures/report-outbox-redlined.md).

**Step 4: Publish**

```
$ python scripts/write_spike_report.py \
    --report redlines/spike-PLTPM-21500-report.md \
    --state-fixture ~/IdeaProjects/spike/PLTPM-21500/.spike-state.json
{
  "status": "ok",
  "research_key": "PLTPM-21500",
  "page_title": "Spike: do we need a transactional outbox for wallet-to-wallet events?",
  "elapsed_minutes": 28,
  "budget_minutes": 30,
  "update_existing": false,
  "actions": [
    {"step": 1, "phase": "publish", "tool": "atlassian.createConfluencePage",
     "args": {"spaceId": "1048576", "parentPageId": "3333", "title": "...",
              "body": {"storage": {"value": "<h2>Question</h2>...", "representation": "storage"}}}},
    {"step": 2, "phase": "comment", "tool": "atlassian.addCommentToJiraIssue",
     "args": {"issueKey": "PLTPM-21500",
              "body": "Spike report published: {{confluence_page_url}}. ..."}},
    {"step": 3, "phase": "transition", "tool": "atlassian.transitionJiraIssue",
     "args": {"issueKey": "PLTPM-21500", "transition": {"name": "Done"}}}
  ]
}
```

The agent:
1. Calls `atlassian.createConfluencePage`. Captures the response's `_links.webui` URL.
2. Substitutes `{{confluence_page_url}}` with the captured URL and calls `atlassian.addCommentToJiraIssue`.
3. Calls `atlassian.getJiraIssueTransitions(issueKey="PLTPM-21500")` to find the transition id whose name matches `Done`, then calls `atlassian.transitionJiraIssue` with that id.

The compare against the golden:

```
$ diff <(python scripts/write_spike_report.py \
          --report redlines/spike-PLTPM-21500-report.md \
          --state-fixture ~/.../state-outbox.json \
          --now '2026-05-02T18:18:00+00:00') \
       scripts/tests/fixtures/outbox-write-action-plan.expected.json
# → no diff
```

(The `--now` flag pins the elapsed-minutes computation for the golden test.)

**Step 5: Cleanup**

The engineer (or a follow-up session) removes the worktrees. The cleanup commands are in each worktree's `SPIKE_README.md`:

```
$ git -C ~/IdeaProjects/payment-platform worktree remove ~/IdeaProjects/spike/PLTPM-21500/payment-platform --force
$ git -C ~/IdeaProjects/payment-platform branch -D spike/PLTPM-21500
$ git -C ~/IdeaProjects/walletapi worktree remove ~/IdeaProjects/spike/PLTPM-21500/walletapi --force
$ git -C ~/IdeaProjects/walletapi branch -D spike/PLTPM-21500
$ rm redlines/spike-PLTPM-21500-*.md
```

## Example 2: orphan spike → single-repo standalone investigation

**Setup**

- Research ticket: `PLTPM-40050` "Spike: investigate intermittent webhook delivery failures (no parent ticket)".
- No parent — incident-driven, not tied to a planned PRD.
- Single repo: `payment-platform`.
- Expected output: a Confluence sub-page under the configured Spikes parent (orphan path), comment on the Research ticket, ticket transitioned to Done.

The corresponding fixture: [`research-orphan.json`](scripts/tests/fixtures/research-orphan.json) — `validate_spike_target.py`'s terminal `ok` payload for the orphan path.

### Run-through (deltas vs Example 1)

**Step 1: Validate target** — same first call:

```
$ python scripts/validate_spike_target.py --research-key PLTPM-40050
{"status": "needs_fetch_research", ...}
```

The agent fetches the Research ticket. The response has no parent link. Re-running skips straight to repo selection:

```
$ python scripts/validate_spike_target.py --research-key PLTPM-40050 \
    --research-fixture research.json
{
  "status": "needs_repo_selection",
  "candidate_repos": ["payment-platform", "walletapi", "infrastructure", "spring-boot-starters"],
  "next_step": "Re-run with --repos ..."
}
```

There's no parent fetch and no Confluence-parent fetch. `confluence_parent_path` will be `"orphan"` and `confluence_parent_page_id` will be the configured Spikes parent (`9876543` in `jira-conventions.md`).

The remaining steps (graphify, bootstrap, investigation, publish, cleanup) are identical to Example 1. The only differences in the published page are:

- The page lands under the **Spikes** parent, not under a PRD page.
- The metadata footer renders `Parent ticket: _none (orphan spike)_` instead of a key.

**No-PR rule still applies.** Spike branches and worktrees never become PRs, regardless of whether the spike is parented or orphan.

## Hard rules (worth re-stating in worked-example context)

- The agent **never** runs `git push`, `git push --set-upstream`, or `gh pr create` from inside a spike worktree. `push.default=nothing` is set as a second line of defence; the first line is the agent's policy.
- Trial commits on `spike/<KEY>` are fine and encouraged (they let the agent reference specific shas in the report's "What was tried" section).
- The metadata footer's `BUDGET_EXCEEDED` flag is informational. It does not gate publish; the engineer decides whether to publish a long-running spike or scope down and re-run.
