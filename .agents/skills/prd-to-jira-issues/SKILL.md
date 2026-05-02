---
name: prd-to-jira-issues
description: Slices a parent PRD ticket into per-repo Jira children with ceremony Sub-tasks and dependency links. Use after `write-a-prd` produces a parent Story or Technical Story; queries graphify for slice boundaries and cross-slice ordering, surfaces a redline-ready slice plan, and writes children once PM/eng approves. Skip for solo-repo features that are already small enough to ship as a single Task.
---

# prd-to-jira-issues

Second skill in the gru pipeline. Takes a parent ticket key produced by `write-a-prd` and lands the per-repo child Tasks, Research siblings, ceremony Sub-tasks, and dependency links — ready for `ai-ready-check`.

## Quick start

1. **Invoke**: "use the prd-to-jira-issues skill on PLTPM-XXXXX".
2. **Success looks like**: a list of child Jira keys grouped by slice (`A: PLTPM-XXXXX, B: PLTPM-YYYYY ...`) posted in chat, with ceremony Sub-tasks, Implement links, and `is blocked by` chains in place.
3. **Precondition**: graphify MCP, Atlassian MCP, and the parent ticket already exist (run `write-a-prd` first if not).

## Workflow

11 steps. Pause for the PM/eng only at step 7 (review checkpoint) and on validation errors.

1. **Validate parent**. Run `scripts/validate_parent.py --parent-key PLTPM-XXXXX`. It emits a phase-1 action plan to fetch the parent.
2. **Fetch parent**. Call `atlassian.getJiraIssue` per the plan; save the response as a JSON fixture; re-run `validate_parent.py --parent-fixture <path>`.
3. **Fetch Confluence (if elevated)**. If step 2 exits with `needs_fetch_confluence`, call `atlassian.getConfluencePage` for the linked page; save as a fixture with a `body` field; re-run `validate_parent.py --confluence-fixture <path>`. The script's final stdout is the normalized parent JSON — pipe it to a file.
4. **Propose slices (phase 1)**. Run `scripts/propose_slices.py --parent-fixture <validated-parent.json>`. Emits a graphify query plan.
5. **Fetch graphify**. Run the queries; aggregate the responses into `{modules: [{name, repo}], edges: [{from, to, type}]}`; save as a fixture.
6. **Render slice plan**. Re-run `propose_slices.py --parent-fixture ... --graphify-fixture ...`. Writes `redlines/PLTPM-XXXXX.md` and prints a JSON summary.
7. **Review checkpoint**. Open `redlines/PLTPM-XXXXX.md`. Drop slices, reorder, edit `depends_on:`, and **fill in the acceptance criteria** for every slice. The literal `TODO` placeholder must be removed before step 8 will succeed.
8. **Plan children**. Run `scripts/write_jira_children.py --slice-plan redlines/PLTPM-XXXXX.md --parent-key PLTPM-XXXXX`. Emits a deterministic 4-phase JSON action plan.
9. **Execute**. For each action, call the resolved Atlassian MCP tool. Substitute `{{slice_<letter>_key}}` placeholders from previous `createJiraIssue` results. Phases run in order: createIssue per slice → 5 ceremony Sub-tasks per Task/Tech-Story slice → Implement link parent→child → Blocks links per `depends_on:`.
10. **Cleanup**. Delete `redlines/PLTPM-XXXXX.md`. The redlines directory is gitignored.
11. **Handoff**. Post the child key list grouped by slice in chat. Recommend running `ai-ready-check` next.

## Reading and editing the slice plan

Treat `redlines/PLTPM-XXXXX.md` as the working surface. Conventions:

- One `## Slice <letter> — <one-liner>` section per child issue. Letter ordering is significant (alphabetic = creation order).
- The header keyvalue block (`- type:`, `- title:`, `- component:`, `- depends_on:`, `- description: |`) is parsed by `write_jira_children.py`. Indentation under `description: |` becomes the issue body.
- **Drop** a slice by deleting its entire section.
- **Reorder** by reordering sections (re-letter as needed for `depends_on:` references).
- **Add** a slice by copying an existing one and editing fields.
- **Acceptance criteria** must be filled in by PM/eng. The script refuses to write if any description still contains the literal `TODO`.
- **Re-running propose_slices is destructive**: the script refuses to overwrite an existing redline file unless `--force` is passed.

Allowed slice types: `Task`, `Technical Story`, `Research`, `Design`. Trailing space on `Research`/`Design` is optional in the redline (the script handles it).

## Advanced

- **Slice-plan markdown spec, action-plan JSON contracts, MCP call patterns**: [REFERENCE.md](REFERENCE.md).
- **Worked walkthroughs (single-repo + multi-repo elevated)**: [EXAMPLES.md](EXAMPLES.md).
- **Heuristics** (cross-app slicing, weakly-connected splits, ordering, Research detection): docstrings in `scripts/propose_slices.py`.
- **Conventions** (issue types, Components policy, link types): `.agents/jira-conventions.md`.

## Out of scope for this skill

- Drafting the parent PRD — that's `write-a-prd`.
- Applying the `ai-ready` label — that's `ai-ready-check`.
- Spike execution — that's `spike-and-report`.
- Auto-transitioning `To Do → next` — human eng-reviewed gate.
- Editing files in product repos. gru treats the four canonical repos as read-only.
