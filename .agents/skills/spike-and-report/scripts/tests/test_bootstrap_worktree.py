"""Unit + integration tests for `bootstrap_worktree.py`.

Covers:
- target validation (missing keys, wrong status, empty repos).
- Action ordering (worktree-add -> push.default -> state file -> SPIKE_README* ->
  briefing -> report).
- Multi-repo expansion (one git pair per repo; one SPIKE_README per repo).
- State-file shape (all required keys, ISO-8601 timestamp pass-through, budget constants).
- Briefing markdown structure (question, graphify summary, worktree paths, budget).
- Report skeleton: 7 required sections, `TODO` placeholders in the agent-fill
  sections, auto-populated marker on the auto sections.
- SPIKE_README content: hard rules, cleanup command, budget reminder.
- Conflict refusal: existing state file, existing non-empty worktree dir; --reset-worktree opt-out.
- Action ordering with --reset-worktree (worktree-remove + branch -D come first).
- CLI integration via run_main.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import bootstrap_worktree as bw

FIXED_TIME = "2026-05-02T18:00:00+00:00"


# ---------- target validation ----------


class TestValidateTarget:
    def test_accepts_minimal(self, target_payload):
        bw.validate_target(target_payload)  # does not raise

    def test_rejects_missing_keys(self, target_payload):
        del target_payload["repos"]
        with pytest.raises(bw.BootstrapError) as exc:
            bw.validate_target(target_payload)
        assert "repos" in str(exc.value)

    def test_rejects_wrong_status(self, target_payload):
        target_payload["status"] = "needs_repo_selection"
        with pytest.raises(bw.BootstrapError):
            bw.validate_target(target_payload)

    def test_accepts_missing_status_field(self, target_payload):
        # Status is OK to be absent — bootstrap consumes the same JSON shape
        # both as a piped stdin and as a hand-curated target file.
        del target_payload["status"]
        bw.validate_target(target_payload)

    def test_rejects_empty_repos(self, target_payload):
        target_payload["repos"] = []
        with pytest.raises(bw.BootstrapError) as exc:
            bw.validate_target(target_payload)
        assert "repos" in str(exc.value)

    def test_rejects_non_dict_graphify(self, target_payload):
        target_payload["graphify"] = "not a dict"
        with pytest.raises(bw.BootstrapError):
            bw.validate_target(target_payload)


# ---------- action plan structure ----------


def _build(target_payload, tmp_path: Path, *, reset: bool = False) -> dict:
    return bw.build_action_plan(
        target=target_payload,
        worktree_base=tmp_path / "wt",
        repo_source_base=tmp_path / "src",
        redline_dir=tmp_path / "redlines",
        reset=reset,
        started_at=FIXED_TIME,
    )


class TestActionPlanStructure:
    def test_status_ready(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        assert plan["status"] == "ready"

    def test_branch_format(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        assert plan["branch"] == "spike/PLTPM-21500"

    def test_steps_are_strictly_increasing(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        steps = [a["step"] for a in plan["actions"]]
        assert steps == sorted(steps) == list(range(1, len(steps) + 1))

    def test_first_action_is_worktree_add(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        first = plan["actions"][0]
        assert first["type"] == "shell"
        assert first["command"][:2] == ["git", "-C"]
        assert "worktree" in first["command"]
        assert "add" in first["command"]

    def test_second_action_is_push_default_config(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        second = plan["actions"][1]
        assert second["type"] == "shell"
        assert "config" in second["command"]
        assert "push.default" in second["command"]
        assert "nothing" in second["command"]

    def test_state_file_action_after_git_actions(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        # Single repo → 2 git actions, then state file.
        state_action = plan["actions"][2]
        assert state_action["type"] == "write_file"
        assert state_action["path"].endswith("/.spike-state.json")

    def test_spike_readme_after_state(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        readme_action = plan["actions"][3]
        assert readme_action["type"] == "write_file"
        assert readme_action["path"].endswith("/SPIKE_README.md")

    def test_briefing_then_report_skeleton_at_end(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path)
        last_two = plan["actions"][-2:]
        assert last_two[0]["path"].endswith("-briefing.md")
        assert last_two[1]["path"].endswith("-report.md")


class TestMultiRepoExpansion:
    def test_one_git_pair_per_repo(self, target_multi_repo_payload, tmp_path: Path):
        plan = _build(target_multi_repo_payload, tmp_path)
        worktree_adds = [
            a for a in plan["actions"]
            if a["type"] == "shell" and "worktree" in a["command"] and "add" in a["command"]
        ]
        push_configs = [
            a for a in plan["actions"]
            if a["type"] == "shell" and "push.default" in a["command"]
        ]
        assert len(worktree_adds) == 2
        assert len(push_configs) == 2

    def test_one_readme_per_repo(self, target_multi_repo_payload, tmp_path: Path):
        plan = _build(target_multi_repo_payload, tmp_path)
        readmes = [
            a for a in plan["actions"]
            if a["type"] == "write_file" and a["path"].endswith("/SPIKE_README.md")
        ]
        assert len(readmes) == 2
        # Two distinct paths.
        assert len({r["path"] for r in readmes}) == 2

    def test_repo_order_preserved_in_worktree_actions(
        self, target_multi_repo_payload, tmp_path: Path
    ):
        plan = _build(target_multi_repo_payload, tmp_path)
        worktree_adds = [
            a for a in plan["actions"]
            if a["type"] == "shell" and "worktree" in a["command"] and "add" in a["command"]
        ]
        # First worktree should target payment-platform (first repo in fixture).
        assert "payment-platform" in worktree_adds[0]["command"][2]
        assert "walletapi" in worktree_adds[1]["command"][2]


class TestResetWorktree:
    def test_reset_emits_remove_then_branch_d_per_repo(
        self, target_multi_repo_payload, tmp_path: Path
    ):
        plan = _build(target_multi_repo_payload, tmp_path, reset=True)
        # First 4 actions: remove + branch -D for each of 2 repos.
        first_four = plan["actions"][:4]
        kinds = [
            (
                "remove" if "worktree" in a["command"] and "remove" in a["command"]
                else "branch_d" if "branch" in a["command"] and "-D" in a["command"]
                else "?"
            )
            for a in first_four
        ]
        assert kinds == ["remove", "branch_d", "remove", "branch_d"]

    def test_no_reset_actions_without_flag(self, target_payload, tmp_path: Path):
        plan = _build(target_payload, tmp_path, reset=False)
        for a in plan["actions"]:
            if a["type"] == "shell":
                cmd = a["command"]
                assert not (
                    "worktree" in cmd and "remove" in cmd
                ), "no remove without --reset-worktree"


# ---------- state file content ----------


class TestStateFile:
    def test_carries_all_required_keys(self, target_payload, tmp_path: Path):
        body = bw.build_state_file(target_payload, tmp_path / "wt", FIXED_TIME)
        state = json.loads(body)
        for key in (
            "schema_version",
            "started_at",
            "research_key",
            "parent_key",
            "confluence_parent_page_id",
            "confluence_parent_path",
            "spikes_parent_page_id",
            "repos",
            "worktree_paths",
            "wall_clock_budget_minutes",
            "turn_budget",
        ):
            assert key in state

    def test_started_at_is_iso8601_when_injected(self, target_payload, tmp_path: Path):
        body = bw.build_state_file(target_payload, tmp_path / "wt", FIXED_TIME)
        state = json.loads(body)
        assert state["started_at"] == FIXED_TIME

    def test_started_at_default_is_iso8601_with_timezone(
        self, target_payload, tmp_path: Path
    ):
        body = bw.build_state_file(target_payload, tmp_path / "wt", None)
        state = json.loads(body)
        # Loose check: ISO date prefix + tz offset suffix.
        s = state["started_at"]
        assert "T" in s
        assert s.endswith("+00:00")

    def test_worktree_paths_dict_shape(self, target_multi_repo_payload, tmp_path: Path):
        body = bw.build_state_file(
            target_multi_repo_payload, tmp_path / "wt", FIXED_TIME
        )
        state = json.loads(body)
        wts = state["worktree_paths"]
        assert set(wts.keys()) == {"payment-platform", "walletapi"}
        for repo, path in wts.items():
            assert path.endswith(f"/spike/PLTPM-21500/{repo}")

    def test_budget_constants(self, target_payload, tmp_path: Path):
        body = bw.build_state_file(target_payload, tmp_path / "wt", FIXED_TIME)
        state = json.loads(body)
        assert state["wall_clock_budget_minutes"] == bw.WALL_CLOCK_BUDGET_MINUTES
        assert state["turn_budget"] == bw.TURN_BUDGET


# ---------- SPIKE_README ----------


class TestSpikeReadme:
    def test_contains_no_pr_warning(self, target_payload, tmp_path: Path):
        body = bw.build_spike_readme(target_payload, tmp_path / "wt", "payment-platform")
        assert "No PR is ever opened" in body
        assert "push.default=nothing" in body

    def test_contains_cleanup_command(self, target_payload, tmp_path: Path):
        body = bw.build_spike_readme(target_payload, tmp_path / "wt", "payment-platform")
        assert "git -C" in body
        assert "worktree remove" in body
        assert "branch -D" in body

    def test_contains_budget_reminder(self, target_payload, tmp_path: Path):
        body = bw.build_spike_readme(target_payload, tmp_path / "wt", "payment-platform")
        assert str(bw.WALL_CLOCK_BUDGET_MINUTES) in body
        assert str(bw.TURN_BUDGET) in body

    def test_carries_correct_branch(self, target_payload, tmp_path: Path):
        body = bw.build_spike_readme(target_payload, tmp_path / "wt", "payment-platform")
        assert "spike/PLTPM-21500" in body

    def test_per_repo_path_in_cleanup(self, target_payload, tmp_path: Path):
        body = bw.build_spike_readme(target_payload, tmp_path / "wt", "walletapi")
        assert "walletapi" in body


# ---------- briefing ----------


class TestBriefing:
    def test_includes_research_key_and_summary(
        self, target_payload, tmp_path: Path
    ):
        body = bw.build_briefing(target_payload, tmp_path / "wt", tmp_path / "redlines")
        assert "PLTPM-21500" in body
        assert "transactional outbox" in body

    def test_includes_graphify_summary_section(self, target_payload, tmp_path: Path):
        body = bw.build_briefing(target_payload, tmp_path / "wt", tmp_path / "redlines")
        assert "## Existing state (from graphify)" in body
        assert "WalletEventPublisher" in body

    def test_includes_each_worktree_path(
        self, target_multi_repo_payload, tmp_path: Path
    ):
        body = bw.build_briefing(
            target_multi_repo_payload, tmp_path / "wt", tmp_path / "redlines"
        )
        assert "payment-platform" in body
        assert "walletapi" in body

    def test_includes_budget_section(self, target_payload, tmp_path: Path):
        body = bw.build_briefing(target_payload, tmp_path / "wt", tmp_path / "redlines")
        assert "## Budget" in body
        assert str(bw.WALL_CLOCK_BUDGET_MINUTES) in body

    def test_points_at_report_path(self, target_payload, tmp_path: Path):
        body = bw.build_briefing(target_payload, tmp_path / "wt", tmp_path / "redlines")
        assert "spike-PLTPM-21500-report.md" in body

    def test_handles_empty_graphify_gracefully(self, target_payload, tmp_path: Path):
        target_payload["graphify"] = {"nodes": [], "edges": [], "communities": []}
        body = bw.build_briefing(target_payload, tmp_path / "wt", tmp_path / "redlines")
        assert "no scoped nodes" in body


# ---------- report skeleton ----------


_REPORT_REQUIRED_HEADINGS = (
    "## Question",
    "## Existing state (from graphify)",
    "## What was tried",
    "## What worked / what didn't",
    "## Recommended approach",
    "## Suggested follow-up tickets",
    "## Spike metadata",
)


class TestReportSkeleton:
    def test_has_all_seven_required_headings(self, target_payload):
        body = bw.build_report_skeleton(target_payload)
        for heading in _REPORT_REQUIRED_HEADINGS:
            assert heading in body, f"missing heading: {heading}"

    def test_status_draft_lead_in(self, target_payload):
        body = bw.build_report_skeleton(target_payload)
        assert "Status:" in body and "DRAFT" in body

    def test_todo_placeholders_present_in_agent_fill_sections(self, target_payload):
        body = bw.build_report_skeleton(target_payload)
        # 5 sections require fill-in: What was tried, What worked, Recommended,
        # Suggested follow-up. Question carries the actual question (no TODO).
        # Count TODO occurrences — should be ≥ 4 (one per agent-fill section
        # excluding Question, plus possibly more in inline guidance).
        assert body.count("TODO") >= 4

    def test_auto_populated_marker_on_auto_sections(self, target_payload):
        body = bw.build_report_skeleton(target_payload)
        # Existing state + Spike metadata are auto-populated; the skeleton
        # tells the agent not to overwrite them.
        assert body.count("auto-populated") >= 2

    def test_question_section_carries_actual_question(self, target_payload):
        body = bw.build_report_skeleton(target_payload)
        assert "transactional outbox" in body


# ---------- CLI / run() ----------


class TestCLI:
    def test_happy_path_emits_ready_plan(
        self, run_main, tmp_path: Path, target_fixture: Path
    ):
        code, out, _err = run_main(
            bw.main,
            "--target-fixture", str(target_fixture),
            "--worktree-base", str(tmp_path / "wt"),
            "--repo-source-base", str(tmp_path / "src"),
            "--redline-dir", str(tmp_path / "redlines"),
            "--started-at", FIXED_TIME,
        )
        assert code == 0
        assert out["status"] == "ready"
        assert out["branch"] == "spike/PLTPM-21500"

    def test_existing_state_refusal(
        self, run_main, tmp_path: Path, target_fixture: Path
    ):
        wt = tmp_path / "wt" / "spike" / "PLTPM-21500"
        wt.mkdir(parents=True)
        (wt / ".spike-state.json").write_text("{}", encoding="utf-8")
        code, _out, err = run_main(
            bw.main,
            "--target-fixture", str(target_fixture),
            "--worktree-base", str(tmp_path / "wt"),
            "--repo-source-base", str(tmp_path / "src"),
            "--redline-dir", str(tmp_path / "redlines"),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "existing_state"

    def test_existing_worktree_refusal(
        self, run_main, tmp_path: Path, target_fixture: Path
    ):
        wt = tmp_path / "wt" / "spike" / "PLTPM-21500" / "payment-platform"
        wt.mkdir(parents=True)
        (wt / "marker").write_text("x", encoding="utf-8")
        code, _out, err = run_main(
            bw.main,
            "--target-fixture", str(target_fixture),
            "--worktree-base", str(tmp_path / "wt"),
            "--repo-source-base", str(tmp_path / "src"),
            "--redline-dir", str(tmp_path / "redlines"),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "existing_worktree"

    def test_reset_worktree_skips_refusal(
        self, run_main, tmp_path: Path, target_fixture: Path
    ):
        wt = tmp_path / "wt" / "spike" / "PLTPM-21500" / "payment-platform"
        wt.mkdir(parents=True)
        (wt / "marker").write_text("x", encoding="utf-8")
        code, out, _err = run_main(
            bw.main,
            "--target-fixture", str(target_fixture),
            "--worktree-base", str(tmp_path / "wt"),
            "--repo-source-base", str(tmp_path / "src"),
            "--redline-dir", str(tmp_path / "redlines"),
            "--reset-worktree",
            "--started-at", FIXED_TIME,
        )
        assert code == 0
        assert out["status"] == "ready"
        # Plan should start with cleanup actions when --reset-worktree is set.
        assert "remove" in out["actions"][0]["command"]

    def test_briefing_matches_golden(self):
        """Byte-stable check: briefing rendered from the canonical
        elevated-parent fixture must match the checked-in golden exactly.

        This is the contract that the briefing prose is reproducible across
        runs — engineers reviewing fixtures see the same output the script
        will emit.
        """
        fixtures = Path(__file__).parent / "fixtures"
        target = json.loads(
            (fixtures / "research-elevated-parent.json").read_text(encoding="utf-8")
        )
        actual = bw.build_briefing(
            target,
            Path("/Users/fvoon/IdeaProjects"),
            Path(
                "/Users/fvoon/IdeaProjects/gru/.agents/skills/spike-and-report/redlines"
            ),
        )
        expected = (fixtures / "briefing-outbox.expected.md").read_text(encoding="utf-8")
        assert actual == expected, "briefing diverged from golden — re-run generator if intended"

    def test_invalid_target_exits_2(self, run_main, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"status": "ok", "research_key": "x"}), encoding="utf-8")
        code, _out, err = run_main(
            bw.main,
            "--target-fixture", str(bad),
            "--worktree-base", str(tmp_path / "wt"),
            "--repo-source-base", str(tmp_path / "src"),
            "--redline-dir", str(tmp_path / "redlines"),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "error"
        assert "missing required keys" in body["reason"]
