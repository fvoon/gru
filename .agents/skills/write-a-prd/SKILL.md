---
name: write-a-prd
description: Drafts a PRD via interactive interview, queries graphify for cross-app cohesion context, validates output against PLTPM Jira conventions, and writes a parent Story or Technical Story (with Confluence page if significant). Use when a PM wants to scope a new feature end-to-end, when an integration touching multiple repos needs cohesion-aware planning, or when a high-level idea needs to be turned into ticket-ready scope.
---

# write-a-prd

Entry point of the gru pipeline. The PM says "I want to add X"; this skill ends with a parent Jira ticket (and optionally a Confluence page) ready for `prd-to-jira-issues`.

## Quick start

1. **Invoke**: PM asks the agent "use the write-a-prd skill, I want to <feature idea>".
2. **Success looks like**: a PLTPM Jira key (e.g. `PLTPM-XXXXX`) posted in chat, with the local draft cleaned up.
3. **Precondition**: Atlassian MCP is mounted in the runtime. Graphify queries are served by the `_lib/graphify.py` harness helper — see "Harness helpers" in gru's `AGENTS.md`. No standalone graphify MCP is required.

## Workflow

The agent runs these 10 steps in order. Stop and ask the PM for input only at step 5 (review checkpoint) and on `requires_user_choice` from step 8.

1. **Probe**. Query the `_lib/graphify.py` harness helper for concepts / modules likely related to the PM's idea across the 4 canonical repos (`payment-platform`, `walletapi`, `infrastructure`, `spring-boot-starters`) — typically `python _lib/graphify.py search --text "<idea>"` followed by `get_node` / `get_edges` on the top hits. Surface the top hits as a "likely affected apps" summary. If the helper returns `needs_synthesis` (no `graph.json` reachable), skip directly to step 2 and rely on narrative knowledge.
2. **Narrate**. Read matching files under `gru/knowledge/narrative/` on demand (`flows/<flow>.md`, `glossary.md`, `repos/<repo>.md`). Use these to anchor the interview, not as a script.
3. **Interview**. Ask the PM section-by-section for the 8 sections (see [REFERENCE.md](REFERENCE.md) for the template). Pre-fill graphify findings and let the PM confirm / correct.
4. **Append**. Write each section to `drafts/<slug>.md` as it stabilises. Slug = kebab-case of the working title. The drafts directory is gitignored.
5. **Review checkpoint**. Show the full draft to the PM. Iterate (back to step 3) until the PM says "ship it".
6. **Validate components**. Run `scripts/validate_components.py`. If it returns `needs_fetch`, call `atlassian.getJiraProjectComponents` for `PLTPM` and re-run with `--components-fixture <saved.json>`. If it exits 1 (missing), refuse to proceed and point the PM at the bootstrap checklist in `.agents/jira-conventions.md`.
7. **Score significance**. Run `scripts/significance_check.py --draft drafts/<slug>.md`. Capture stdout to `<slug>.decision.json`.
8. **Plan write**. Run `scripts/write_jira_prd.py --draft drafts/<slug>.md --decision-json <slug>.decision.json`. If `requires_user_choice` is set, ask the PM which canonical repo "owns" the parent, then re-run with `--mode <inline|elevate>` plus an updated draft (or pass `--primary-component` once that flag is added; today, fix the draft).
9. **Execute**. For each action in the plan, call the resolved Atlassian MCP tool. Substitute `{{confluence_page_url}}` and `{{parent_issue_key}}` from earlier steps. The script never calls MCP itself — the agent is the executor.
10. **Cleanup**. Post the new Jira key (and Confluence URL if elevated) in chat, then delete `drafts/<slug>.md` and `<slug>.decision.json`. Do not commit drafts.

## Reading the draft

The PM should treat `drafts/<slug>.md` as the working surface, not the chat. Encourage them to:

- Edit the draft directly between iterations (faster than dictating diffs).
- Drop a `<!-- elevate-to-confluence -->` HTML comment anywhere if they want to force the elevate path regardless of size.
- Keep section headers verbatim (`## Problem Statement`, etc.) — the scripts split on them.

The 8 required sections are: `Problem Statement`, `Solution`, `User Stories`, `Cross-Application Impact`, `Implementation Decisions`, `Out of Scope`, `Further Notes`, `References`.

## Advanced

- **PRD template + section semantics**: [REFERENCE.md](REFERENCE.md).
- **Worked walkthroughs (one inline, one elevated)**: [EXAMPLES.md](EXAMPLES.md).
- **Action-plan JSON contracts** (per script) and **Atlassian MCP call patterns**: [REFERENCE.md](REFERENCE.md).
- **Heuristics** (parent-type, significance, Component derivation): docstrings in `scripts/write_jira_prd.py` and `scripts/significance_check.py`.
- **Conventions** (issue types, Components policy, link types, status workflow): `.agents/jira-conventions.md` (one level up).

## Out of scope for this skill

- Creating child tickets — that's `prd-to-jira-issues`.
- Spike / research execution — that's `spike-and-report`.
- Applying the `ai-ready` label — that's `ai-ready-check`.
- Editing files in product repos. gru treats `payment-platform`, `walletapi`, `infrastructure`, and `spring-boot-starters` as read-only.
