"""Bootstrap a throwaway worktree for the spike-and-report skill.

Pure script: no shell-outs, no MCP, no clock unless explicitly injected.

Given a normalized spike target JSON (the `ok`-status output of
`validate_spike_target.py`) plus an `--out-dir` for redline files, this script
emits a deterministic plan that the LLM in chat executes:

  1. `git worktree add` — one command per repo named in the target's `repos`.
     Each worktree lives at `<worktree-base>/spike/<KEY>/<repo>/` and is
     checked out on a fresh branch `spike/<KEY>` (or its existing instance, if
     the branch survived from a prior aborted spike).
  2. `git config push.default nothing` — set local-only in each worktree to
     make accidental `git push` a no-op. Documented as a hard rule in
     SKILL.md too; this is a belt+braces second line of defence.
  3. Write `.spike-state.json` at `<worktree-base>/spike/<KEY>/.spike-state.json`
     with timing, parent + Confluence resolution, and worktree paths. The
     write_spike_report.py script consumes this for budget stamping +
     Confluence parent id.
  4. Write `SPIKE_README.md` in each worktree carrying the no-PR rule, the
     worktree cleanup command, and the wall-clock budget reminder.
  5. Write `redlines/spike-<KEY>-briefing.md` — read-only investigation
     context: the Research question, graphify summary, worktree paths, time
     budget, and the path of the report file the agent should append to.
  6. Write `redlines/spike-<KEY>-report.md` — 7-section template with `TODO`
     placeholders the agent fills in during investigation. write_spike_report
     refuses to publish until the placeholders are gone.

Idempotency:
- If `.spike-state.json` already exists at the target path and `--reset-worktree`
  is not set, the script refuses (status `existing_state`).
- If the worktree base directory exists with content and `--reset-worktree` is
  not set, the script refuses (status `existing_worktree`).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

# 30 minutes is the soft wall-clock budget per docs/plan.md item 11. Captured
# here so the SKILL.md prose, briefing, SPIKE_README, and write_spike_report
# footer all reference the same number.
WALL_CLOCK_BUDGET_MINUTES = 30
TURN_BUDGET = 30


# ----- exceptions -----


class BootstrapError(Exception):
    """Bad target JSON or other input error (exit 2)."""


class ConflictRefusal(Exception):
    """Existing state / worktree (exit 2; override with --reset-worktree)."""

    def __init__(self, status: str, reason: str, path: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.path = path


# ----- target validation -----


_REQUIRED_TARGET_KEYS = (
    "research_key",
    "research_summary",
    "research_question",
    "parent_key",
    "confluence_parent_page_id",
    "confluence_parent_path",
    "spikes_parent_page_id",
    "repos",
    "graphify",
)


def validate_target(target: dict) -> None:
    """Sanity-check that the target JSON has every key bootstrap depends on."""
    missing = [k for k in _REQUIRED_TARGET_KEYS if k not in target]
    if missing:
        raise BootstrapError(
            f"target JSON missing required keys: {missing}. Did you pass the "
            f"`ok`-status output of validate_spike_target.py?"
        )
    if target.get("status") not in (None, "ok"):
        raise BootstrapError(
            f"target JSON has status {target['status']!r}; bootstrap_worktree "
            "only runs against the terminal `ok` payload."
        )
    if not isinstance(target["repos"], list) or not target["repos"]:
        raise BootstrapError("target.repos must be a non-empty list")
    if not isinstance(target["graphify"], dict):
        raise BootstrapError("target.graphify must be a dict")


# ----- path helpers -----


def worktree_root(worktree_base: Path, research_key: str) -> Path:
    """`<worktree-base>/spike/<KEY>/` — the parent of all per-repo worktrees."""
    return worktree_base / "spike" / research_key


def worktree_path(worktree_base: Path, research_key: str, repo: str) -> Path:
    """`<worktree-base>/spike/<KEY>/<repo>/` — one per repo."""
    return worktree_root(worktree_base, research_key) / repo


def state_path(worktree_base: Path, research_key: str) -> Path:
    return worktree_root(worktree_base, research_key) / ".spike-state.json"


def briefing_path(redline_dir: Path, research_key: str) -> Path:
    return redline_dir / f"spike-{research_key}-briefing.md"


def report_path(redline_dir: Path, research_key: str) -> Path:
    return redline_dir / f"spike-{research_key}-report.md"


# ----- shell action emitters -----


def _shell_action(step: int, command: list[str], purpose: str) -> dict:
    """Build a single shell-action dict. The agent shells out per command."""
    return {
        "step": step,
        "type": "shell",
        "command": command,
        "purpose": purpose,
    }


def _write_action(step: int, path: Path, contents: str, purpose: str) -> dict:
    """Build a 'write file' action. Agent uses its file-writing tool."""
    return {
        "step": step,
        "type": "write_file",
        "path": str(path),
        "contents": contents,
        "purpose": purpose,
    }


def _build_git_actions(
    target: dict, worktree_base: Path, repo_source_base: Path, reset: bool
) -> list[dict]:
    """git worktree add + push.default config, one pair per repo.

    `repo_source_base` is the parent of the four canonical product repos (e.g.
    `~/IdeaProjects/`). Each repo's worktree is `git worktree add`-ed from its
    upstream checkout there. We accept it as a parameter so tests can point it
    at a tmpdir and we don't hit any real product repo state.
    """
    actions: list[dict] = []
    step = 1
    research_key = target["research_key"]
    branch = f"spike/{research_key}"

    if reset:
        # Best-effort cleanup of any prior aborted spike state. The actual
        # worktree-remove command tolerates non-existent paths.
        for repo in target["repos"]:
            wt = worktree_path(worktree_base, research_key, repo)
            actions.append(
                _shell_action(
                    step,
                    [
                        "git",
                        "-C",
                        str(repo_source_base / repo),
                        "worktree",
                        "remove",
                        str(wt),
                        "--force",
                    ],
                    f"--reset-worktree: drop any existing worktree for {repo}",
                )
            )
            step += 1
            actions.append(
                _shell_action(
                    step,
                    [
                        "git",
                        "-C",
                        str(repo_source_base / repo),
                        "branch",
                        "-D",
                        branch,
                    ],
                    f"--reset-worktree: drop any existing {branch} branch in {repo}",
                )
            )
            step += 1

    for repo in target["repos"]:
        wt = worktree_path(worktree_base, research_key, repo)
        # `-B` creates the branch if absent and resets it if present (cheap
        # idempotency for re-runs after a partial bootstrap; the existing-state
        # refusal stops legitimate re-runs at a different layer).
        actions.append(
            _shell_action(
                step,
                [
                    "git",
                    "-C",
                    str(repo_source_base / repo),
                    "worktree",
                    "add",
                    "-B",
                    branch,
                    str(wt),
                ],
                f"create the throwaway worktree for {repo} on branch {branch}",
            )
        )
        step += 1
        actions.append(
            _shell_action(
                step,
                ["git", "-C", str(wt), "config", "--local", "push.default", "nothing"],
                f"belt+braces no-push enforcement for the {repo} worktree",
            )
        )
        step += 1

    return actions


# ----- file content builders -----


def build_state_file(target: dict, worktree_base: Path, started_at: str | None) -> str:
    """Render the `.spike-state.json` body. `started_at` is ISO-8601 UTC."""
    research_key = target["research_key"]
    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state = {
        "schema_version": 1,
        "started_at": started_at,
        "research_key": research_key,
        "research_summary": target["research_summary"],
        "parent_key": target["parent_key"],
        "confluence_parent_page_id": target["confluence_parent_page_id"],
        "confluence_parent_path": target["confluence_parent_path"],
        "spikes_parent_page_id": target["spikes_parent_page_id"],
        "confluence_space_key": target.get("confluence_space_key"),
        "confluence_space_id": target.get("confluence_space_id"),
        "repos": target["repos"],
        "worktree_paths": {
            repo: str(worktree_path(worktree_base, research_key, repo))
            for repo in target["repos"]
        },
        "wall_clock_budget_minutes": WALL_CLOCK_BUDGET_MINUTES,
        "turn_budget": TURN_BUDGET,
    }
    return json.dumps(state, indent=2, ensure_ascii=False) + "\n"


def build_spike_readme(target: dict, worktree_base: Path, repo: str) -> str:
    """Per-worktree README warning the engineer about the no-PR rule + cleanup."""
    research_key = target["research_key"]
    wt = worktree_path(worktree_base, research_key, repo)
    return (
        f"# spike worktree for {research_key} ({repo})\n\n"
        f"This is a **throwaway worktree** for spike-and-report.\n\n"
        f"## Hard rules\n\n"
        f"- **No PR is ever opened from this branch.** `push.default=nothing` "
        f"is set locally; never run `git push`, `git push --set-upstream`, "
        f"or `gh pr create`.\n"
        f"- Wall-clock budget: ~{WALL_CLOCK_BUDGET_MINUTES} minutes. "
        f"Soft cap; the script footer stamps actual elapsed.\n"
        f"- Turn budget: ~{TURN_BUDGET} agent turns. LLM-self-policed; if "
        f"you've used >25, prepare to write the report with what you have.\n"
        f"- Trial commits are fine on `spike/{research_key}` — they just "
        f"never leave this machine.\n\n"
        f"## Cleanup (after the report ships)\n\n"
        f"```\n"
        f"git -C ~/IdeaProjects/{repo} worktree remove {wt} --force\n"
        f"git -C ~/IdeaProjects/{repo} branch -D spike/{research_key}\n"
        f"```\n\n"
        f"## Where the report lives\n\n"
        f"`redlines/spike-{research_key}-report.md` in the gru repo. Edit "
        f"that file as you work; the engineer redlines it at the review "
        f"checkpoint; `write_spike_report.py` consumes it.\n"
    )


def _format_graphify_summary(graphify: dict, repos: list[str]) -> str:
    """Render the briefing's auto-included 'Existing state (from graphify)' body.

    This is read-only context for the agent; the same content (lightly
    re-formatted) is what the script stamps into the report's section 2.
    """
    lines: list[str] = []
    nodes = graphify.get("nodes") or []
    edges = graphify.get("edges") or []
    communities = graphify.get("communities") or []

    if nodes:
        lines.append("**Nodes returned by graphify (scoped to spike repos):**")
        lines.append("")
        for n in nodes:
            if not isinstance(n, dict):
                continue
            name = n.get("name", "<unknown>")
            repo = n.get("repo", "<unknown-repo>")
            kind = n.get("kind", "")
            kind_suffix = f" ({kind})" if kind else ""
            lines.append(f"- `{name}` — {repo}{kind_suffix}")
        lines.append("")
    else:
        lines.append("_graphify returned no scoped nodes for the spike question._")
        lines.append("")

    if edges:
        lines.append("**Edges (cross-module relationships):**")
        lines.append("")
        for e in edges:
            if not isinstance(e, dict):
                continue
            src = e.get("from", "?")
            dst = e.get("to", "?")
            kind = e.get("type", "?")
            lines.append(f"- `{src}` --{kind}--> `{dst}`")
        lines.append("")

    if communities:
        lines.append("**Communities:**")
        lines.append("")
        for c in communities:
            if not isinstance(c, dict):
                continue
            cname = c.get("name", "<unnamed>")
            members = c.get("members") or []
            members_fmt = ", ".join(f"`{m}`" for m in members[:6])
            more = "" if len(members) <= 6 else f" (+{len(members) - 6} more)"
            lines.append(f"- **{cname}** — {members_fmt}{more}")
        lines.append("")

    lines.append(f"_Spike scoped to repos: {', '.join(repos)}._")
    return "\n".join(lines).rstrip() + "\n"


def build_briefing(target: dict, worktree_base: Path, redline_dir: Path) -> str:
    """The agent's read-only investigation context."""
    research_key = target["research_key"]
    repos = target["repos"]
    wt_lines = [
        f"- `{repo}` -> `{worktree_path(worktree_base, research_key, repo)}`"
        for repo in repos
    ]
    return (
        f"# Spike briefing — {research_key}\n\n"
        f"> Read-only context for the agent. Do not edit this file. Append "
        f"findings to "
        f"`{report_path(redline_dir, research_key)}` instead.\n\n"
        f"## Question (from {research_key})\n\n"
        f"**{target['research_summary']}**\n\n"
        f"{target['research_question']}\n\n"
        f"## Existing state (from graphify)\n\n"
        f"{_format_graphify_summary(target['graphify'], repos)}\n"
        f"## Worktrees\n\n"
        + "\n".join(wt_lines)
        + "\n\n"
        + f"All on branch `spike/{research_key}`, `push.default=nothing`. "
        f"Trial commits are fine; PRs are not.\n\n"
        f"## Budget\n\n"
        f"- Wall-clock: ~{WALL_CLOCK_BUDGET_MINUTES} minutes (soft).\n"
        f"- Turns: ~{TURN_BUDGET} (LLM-self-policed; pivot to writing the "
        f"report by turn 25).\n\n"
        f"## Where to write findings\n\n"
        f"Append to `{report_path(redline_dir, research_key)}`. The 7-section "
        f"template is already seeded with `TODO` placeholders; fill them in "
        f"as you go. `write_spike_report.py` refuses to publish until every "
        f"section is `TODO`-free.\n"
    )


def build_report_skeleton(target: dict) -> str:
    """The 7-section template the agent fills in during investigation.

    Sections 1, 3, 4, 5, 6 carry `TODO` placeholders; sections 2 and 7 are
    auto-populated (graphify summary + spike metadata footer respectively)
    and are filled in by write_spike_report.py at publish time. We seed them
    here with their final headings + a brief `_(auto-populated)_` note so the
    agent knows not to overwrite them mid-investigation.
    """
    research_key = target["research_key"]
    return (
        f"# Spike report — {research_key}\n\n"
        f"> **Status:** DRAFT — fill in the `TODO` placeholders below as the "
        f"investigation progresses; engineer redlines before publish.\n\n"
        f"## Question\n\n"
        f"**{target['research_summary']}**\n\n"
        f"{target['research_question']}\n\n"
        f"## Existing state (from graphify)\n\n"
        f"_(auto-populated by write_spike_report.py at publish time — leave "
        f"as-is during investigation.)_\n\n"
        f"## What was tried\n\n"
        f"TODO — list each approach the agent attempted, with worktree "
        f"commit refs and short code excerpts (≤30 lines per excerpt). Not "
        f"full file dumps.\n\n"
        f"## What worked / what didn't\n\n"
        f"TODO — bullet list. Brutal honesty; reviewers should be able to "
        f"see why the recommendation isn't the obvious one.\n\n"
        f"## Recommended approach\n\n"
        f"TODO — one paragraph. Add a `### Recommended contract changes` "
        f"subsection if API / event / DB shapes are affected.\n\n"
        f"## Suggested follow-up tickets\n\n"
        f"TODO — bullet list of `<title> — <one-line AC>` pairs. Format is "
        f"intentionally compatible with `prd-to-jira-issues` Cross-App "
        f"Impact bullets so a human can paste them into a follow-up PRD.\n\n"
        f"## Spike metadata\n\n"
        f"_(auto-populated by write_spike_report.py at publish time.)_\n"
    )


# ----- driver -----


def build_action_plan(
    target: dict,
    worktree_base: Path,
    repo_source_base: Path,
    redline_dir: Path,
    reset: bool,
    started_at: str | None,
) -> dict:
    """Compose the full ordered action plan for the LLM to execute."""
    research_key = target["research_key"]
    actions: list[dict] = []

    # 1. Worktree git commands (per repo).
    actions.extend(_build_git_actions(target, worktree_base, repo_source_base, reset))
    next_step = (actions[-1]["step"] if actions else 0) + 1

    # 2. State file.
    actions.append(
        _write_action(
            next_step,
            state_path(worktree_base, research_key),
            build_state_file(target, worktree_base, started_at),
            "record budget + parent + worktree paths for downstream scripts",
        )
    )
    next_step += 1

    # 3. Per-worktree SPIKE_README.md.
    for repo in target["repos"]:
        readme_path = worktree_path(worktree_base, research_key, repo) / "SPIKE_README.md"
        actions.append(
            _write_action(
                next_step,
                readme_path,
                build_spike_readme(target, worktree_base, repo),
                f"warn anyone who lands in the {repo} worktree about no-PR + cleanup",
            )
        )
        next_step += 1

    # 4. Briefing (read-only agent context).
    actions.append(
        _write_action(
            next_step,
            briefing_path(redline_dir, research_key),
            build_briefing(target, worktree_base, redline_dir),
            "agent reads this for investigation context (graphify summary, worktrees, budget)",
        )
    )
    next_step += 1

    # 5. Report skeleton (the agent appends findings here).
    actions.append(
        _write_action(
            next_step,
            report_path(redline_dir, research_key),
            build_report_skeleton(target),
            "7-section template; agent fills in TODO placeholders during investigation",
        )
    )

    return {
        "status": "ready",
        "research_key": research_key,
        "branch": f"spike/{research_key}",
        "worktree_root": str(worktree_root(worktree_base, research_key)),
        "redline_dir": str(redline_dir),
        "actions": actions,
        "next_step": (
            "Execute the actions in order. After all complete, agent reads "
            "the briefing and starts the investigation; findings go to "
            f"redlines/spike-{research_key}-report.md. When the engineer is "
            "ready, run write_spike_report.py."
        ),
    }


def run(args: argparse.Namespace) -> tuple[int, dict]:
    try:
        raw = args.target_fixture.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError) as exc:
        return (2, {"status": "error", "reason": f"failed to read target fixture: {exc}"})

    try:
        target = json.loads(raw)
    except json.JSONDecodeError as exc:
        return (2, {"status": "error", "reason": f"target fixture is not valid JSON: {exc}"})

    if not isinstance(target, dict):
        return (2, {"status": "error", "reason": "target fixture must be a JSON object"})

    try:
        validate_target(target)
    except BootstrapError as exc:
        return (2, {"status": "error", "reason": str(exc)})

    research_key = target["research_key"]

    # Idempotency: refuse if state file or non-empty worktree dir exists.
    if not args.reset_worktree:
        sp = state_path(args.worktree_base, research_key)
        if sp.exists():
            return (
                2,
                {
                    "status": "existing_state",
                    "reason": (
                        f".spike-state.json already exists at {sp}; pass "
                        "--reset-worktree to nuke it and re-create"
                    ),
                    "path": str(sp),
                },
            )
        wt_root = worktree_root(args.worktree_base, research_key)
        if wt_root.exists() and any(wt_root.iterdir()):
            return (
                2,
                {
                    "status": "existing_worktree",
                    "reason": (
                        f"{wt_root} already exists and is non-empty; pass "
                        "--reset-worktree to nuke it and re-create"
                    ),
                    "path": str(wt_root),
                },
            )

    plan = build_action_plan(
        target=target,
        worktree_base=args.worktree_base,
        repo_source_base=args.repo_source_base,
        redline_dir=args.redline_dir,
        reset=args.reset_worktree,
        started_at=args.started_at,
    )
    return (0, plan)


# ----- CLI -----


def _default_redline_dir() -> Path:
    return SCRIPT_DIR.parent / "redlines"


def _default_worktree_base() -> Path:
    return Path.home() / "IdeaProjects"


def _default_repo_source_base() -> Path:
    """Where the four canonical repos are checked out (the `git -C` parent)."""
    return Path.home() / "IdeaProjects"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--target-fixture",
        type=Path,
        required=True,
        help="Path to validate_spike_target.py's terminal `ok` JSON output.",
    )
    parser.add_argument(
        "--worktree-base",
        type=Path,
        default=_default_worktree_base(),
        help=(
            "Where spike worktrees live. Each repo's worktree ends up at "
            "<worktree-base>/spike/<KEY>/<repo>/."
        ),
    )
    parser.add_argument(
        "--repo-source-base",
        type=Path,
        default=_default_repo_source_base(),
        help=(
            "Parent directory of the four canonical product repos (used as "
            "the `git -C` target for `git worktree add`)."
        ),
    )
    parser.add_argument(
        "--redline-dir",
        type=Path,
        default=_default_redline_dir(),
        help="Where the briefing + report markdown files are written.",
    )
    parser.add_argument(
        "--reset-worktree",
        action="store_true",
        help=(
            "Skip the existing-state / existing-worktree refusal; emit "
            "explicit `git worktree remove` + `git branch -D` commands at "
            "the head of the action plan."
        ),
    )
    parser.add_argument(
        "--started-at",
        type=str,
        default=None,
        help=(
            "ISO-8601 UTC timestamp to record as the spike start time. "
            "Defaults to current UTC. Tests inject a fixed value for "
            "deterministic state-file output."
        ),
    )
    args = parser.parse_args(argv)

    code, payload = run(args)
    target = sys.stderr if code != 0 else sys.stdout
    print(json.dumps(payload, indent=2, ensure_ascii=False), file=target)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
