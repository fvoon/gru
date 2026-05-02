"""Unit + integration tests for `validate_ai_ready.py`.

Covers:
- Ticket / blocker payload normalization (defensive on shape).
- Issuelink extraction (`is blocked by`, `Implement`).
- Prior-outcome extraction via versioned comment marker.
- Per-checklist-item pass + fail (6 items × at least 1 of each).
- Comment body rendering (header + outcome JSON + per-failure detail).
- Action plan emission for each phase (ticket, blockers, ai_ready, not_ai_ready, no_change).
- CLI integration via `run_main` (happy path, mismatched key, missing input, malformed JSON).
- Sentinel tests pinning `CANONICAL_COMPONENTS` and `AI_READY_LABEL` against
  the live `.agents/jira-conventions.md`.
- Byte-stable goldens for the pass + multi-fail action plans.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import validate_ai_ready as var

# ---------- normalize_ticket ----------


class TestNormalizeTicket:
    def test_extracts_all_top_level_fields(self, ticket_passes_all):
        out = var.normalize_ticket(ticket_passes_all)
        assert out["key"] == "PLTPM-21503"
        assert out["issue_type"] == "Technical Story"
        assert out["status"] == "next"
        assert out["components"] == ["payment-platform"]
        assert out["labels"] == []
        assert out["comments"] == []
        assert isinstance(out["issuelinks"], list)
        assert "Acceptance criteria" in out["description"]

    def test_raises_on_non_dict_payload(self):
        with pytest.raises(var.InvalidInput):
            var.normalize_ticket("not-a-dict")  # type: ignore[arg-type]

    def test_raises_on_missing_key(self):
        with pytest.raises(var.InvalidInput) as exc:
            var.normalize_ticket({"fields": {"summary": "x"}})
        assert "key" in str(exc.value)

    def test_raises_on_adf_description(self):
        payload = {
            "key": "PLTPM-1",
            "fields": {"description": {"type": "doc", "content": []}},
        }
        with pytest.raises(var.InvalidInput) as exc:
            var.normalize_ticket(payload)
        assert "ADF" in str(exc.value) or "markdown" in str(exc.value).lower()

    def test_tolerates_fields_at_top_level(self):
        payload = {
            "key": "PLTPM-1",
            "summary": "x",
            "description": "## Acceptance criteria\n\n- y\n",
            "issuetype": {"name": "Task"},
            "status": {"name": "next"},
            "components": [{"name": "payment-platform"}],
            "labels": ["ai-ready"],
        }
        out = var.normalize_ticket(payload)
        assert out["issue_type"] == "Task"
        assert out["labels"] == ["ai-ready"]

    def test_tolerates_missing_optional_fields(self):
        payload = {"key": "PLTPM-1", "fields": {}}
        out = var.normalize_ticket(payload)
        assert out["components"] == []
        assert out["labels"] == []
        assert out["issuelinks"] == []
        assert out["comments"] == []
        assert out["description"] == ""

    def test_extracts_comments_from_dict_block(self):
        payload = {
            "key": "PLTPM-1",
            "fields": {
                "description": "x",
                "comment": {"comments": [{"body": "hello"}, {"body": "world"}]},
            },
        }
        out = var.normalize_ticket(payload)
        assert out["comments"] == ["hello", "world"]

    def test_extracts_comments_when_block_is_already_a_list(self):
        payload = {
            "key": "PLTPM-1",
            "fields": {"description": "x", "comment": [{"body": "hi"}]},
        }
        out = var.normalize_ticket(payload)
        assert out["comments"] == ["hi"]


# ---------- normalize_blocker ----------


class TestNormalizeBlocker:
    def test_happy_path(self, blocker_done):
        out = var.normalize_blocker(blocker_done)
        assert out == {"key": "PLTPM-21504", "status": "Done"}

    def test_raises_on_missing_key(self):
        with pytest.raises(var.InvalidInput):
            var.normalize_blocker({"fields": {"status": {"name": "Done"}}})

    def test_raises_on_non_dict(self):
        with pytest.raises(var.InvalidInput):
            var.normalize_blocker(["not", "a", "dict"])  # type: ignore[arg-type]


# ---------- extract_blocker_keys ----------


class TestExtractBlockerKeys:
    def test_returns_inward_blocker_key(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(blocker_keys=["PLTPM-9000"]))
        assert var.extract_blocker_keys(ticket) == ["PLTPM-9000"]

    def test_preserves_link_order_and_dedupes(self, make_ticket):
        ticket = var.normalize_ticket(
            make_ticket(blocker_keys=["PLTPM-1", "PLTPM-2", "PLTPM-1"])
        )
        assert var.extract_blocker_keys(ticket) == ["PLTPM-1", "PLTPM-2"]

    def test_ignores_non_blocks_links(self, make_ticket):
        ticket = var.normalize_ticket(
            make_ticket(blocker_keys=[], parent_key="PLTPM-99")
        )
        assert var.extract_blocker_keys(ticket) == []

    def test_returns_empty_when_no_links(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(blocker_keys=[], parent_key=None))
        assert var.extract_blocker_keys(ticket) == []

    def test_handles_explicit_blocked_by_outward_text(self):
        # Tolerant of a fixture that puts blocker on outwardIssue with the
        # `is blocked by` text on the outward direction (some MCP wrappers
        # normalize this way).
        payload = {
            "key": "PLTPM-1",
            "fields": {
                "description": "x",
                "issuelinks": [
                    {
                        "type": {
                            "name": "Blocks",
                            "outward": "is blocked by",
                            "inward": "blocks",
                        },
                        "outwardIssue": {"key": "PLTPM-X"},
                    }
                ],
            },
        }
        ticket = var.normalize_ticket(payload)
        assert var.extract_blocker_keys(ticket) == ["PLTPM-X"]


# ---------- extract_implement_parent_key ----------


class TestExtractImplementParentKey:
    def test_finds_via_outward_implements(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(parent_key="PLTPM-99001"))
        assert var.extract_implement_parent_key(ticket) == "PLTPM-99001"

    def test_finds_via_inward_is_implemented_by(self):
        payload = {
            "key": "PLTPM-1",
            "fields": {
                "description": "x",
                "issuelinks": [
                    {
                        "type": {
                            "name": "Implement",
                            "inward": "is implemented by",
                            "outward": "implements",
                        },
                        "inwardIssue": {"key": "PLTPM-PARENT"},
                    }
                ],
            },
        }
        ticket = var.normalize_ticket(payload)
        assert var.extract_implement_parent_key(ticket) == "PLTPM-PARENT"

    def test_returns_none_when_no_implement_link(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(parent_key=None))
        assert var.extract_implement_parent_key(ticket) is None


# ---------- extract_prior_outcome ----------


def _stable_outcome(**overrides) -> dict:
    base = dict.fromkeys(var.CHECK_KEYS, True)
    base.update(overrides)
    return base


def _comment_with_marker(outcome: dict | None, prefix: str = "", body: str = "") -> str:
    parts = [prefix] if prefix else []
    parts.append(var.COMMENT_MARKER)
    if outcome is not None:
        parts.append(
            "<!-- ai-ready-outcome: "
            + json.dumps(outcome, separators=(",", ":"))
            + " -->"
        )
    if body:
        parts.append(body)
    return "\n".join(parts)


class TestExtractPriorOutcome:
    def test_returns_none_when_no_comments(self):
        assert var.extract_prior_outcome([]) is None

    def test_returns_none_when_no_marker_comment(self):
        assert var.extract_prior_outcome(["random comment"]) is None

    def test_parses_valid_outcome(self):
        outcome = _stable_outcome()
        comment = _comment_with_marker(outcome)
        assert var.extract_prior_outcome([comment]) == outcome

    def test_returns_none_when_marker_present_but_outcome_missing(self):
        comment = var.COMMENT_MARKER + "\n## passed\n"
        assert var.extract_prior_outcome([comment]) is None

    def test_returns_none_when_outcome_json_malformed(self):
        comment = (
            var.COMMENT_MARKER + "\n<!-- ai-ready-outcome: {bad json} -->\n## passed"
        )
        assert var.extract_prior_outcome([comment]) is None

    def test_returns_none_when_outcome_keys_missing(self):
        comment = _comment_with_marker({"ac_present": True})
        assert var.extract_prior_outcome([comment]) is None

    def test_returns_most_recent_when_multiple_markers(self):
        old = _comment_with_marker(_stable_outcome(ac_present=False))
        new = _comment_with_marker(_stable_outcome(ac_present=True))
        # Atlassian returns comments oldest first, newest last.
        out = var.extract_prior_outcome([old, new])
        assert out is not None
        assert out["ac_present"] is True

    def test_coerces_truthy_values_to_bool(self):
        # Defensive: prior outcome with 1/0 instead of true/false is still a
        # legitimate prior; we coerce so equality compares cleanly.
        outcome = {k: 1 for k in var.CHECK_KEYS}
        comment = _comment_with_marker(outcome)
        out = var.extract_prior_outcome([comment])
        assert out is not None
        assert all(v is True for v in out.values())


# ---------- per-checklist items ----------


class TestCheckACPresent:
    def test_passes_when_section_present_with_content(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket())
        assert var.check_ac_present(ticket) is True

    def test_fails_when_section_missing(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(description="No AC at all."))
        assert var.check_ac_present(ticket) is False

    def test_fails_when_section_present_but_only_whitespace(self, make_ticket):
        desc = "## Acceptance criteria\n\n   \n\n"
        ticket = var.normalize_ticket(make_ticket(description=desc))
        assert var.check_ac_present(ticket) is False


class TestCheckBlockersDone:
    def test_passes_with_no_blockers(self):
        assert var.check_blockers_done([]) is True

    def test_passes_when_all_done(self):
        blockers = [{"key": "X", "status": "Done"}, {"key": "Y", "status": "Done"}]
        assert var.check_blockers_done(blockers) is True

    def test_fails_when_one_in_progress(self):
        blockers = [{"key": "X", "status": "Done"}, {"key": "Y", "status": "In Progress"}]
        assert var.check_blockers_done(blockers) is False


class TestCheckComponentsCanonical:
    def test_passes_with_one_canonical(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(components=["walletapi"]))
        assert var.check_components_canonical(ticket) is True

    def test_fails_with_zero(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(components=[]))
        assert var.check_components_canonical(ticket) is False

    def test_fails_with_two_canonical(self, make_ticket):
        ticket = var.normalize_ticket(
            make_ticket(components=["payment-platform", "walletapi"])
        )
        assert var.check_components_canonical(ticket) is False

    def test_fails_with_one_non_canonical(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(components=["wrong-component"]))
        assert var.check_components_canonical(ticket) is False


class TestCheckImplementLink:
    def test_passes_when_parent_linked(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(parent_key="PLTPM-99001"))
        assert var.check_implement_link(ticket) is True

    def test_fails_when_no_parent(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(parent_key=None))
        assert var.check_implement_link(ticket) is False


class TestCheckStatusNext:
    def test_passes_for_next(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(status="next"))
        assert var.check_status_next(ticket) is True

    @pytest.mark.parametrize("status", ["To Do", "In Progress", "Done"])
    def test_fails_for_other_statuses(self, make_ticket, status: str):
        ticket = var.normalize_ticket(make_ticket(status=status))
        assert var.check_status_next(ticket) is False


class TestCheckIssueTypeAllowed:
    @pytest.mark.parametrize("issue_type", ["Task", "Technical Story"])
    def test_passes_for_allowed_types(self, make_ticket, issue_type: str):
        ticket = var.normalize_ticket(make_ticket(issue_type=issue_type))
        assert var.check_issue_type_allowed(ticket) is True

    @pytest.mark.parametrize("issue_type", ["Story", "Research ", "Sub-task", "Bug"])
    def test_fails_for_other_types(self, make_ticket, issue_type: str):
        ticket = var.normalize_ticket(make_ticket(issue_type=issue_type))
        assert var.check_issue_type_allowed(ticket) is False


class TestRunChecklistOrder:
    def test_keys_match_check_keys_in_order(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket())
        out = var.run_checklist(ticket, [])
        assert list(out.keys()) == list(var.CHECK_KEYS)


# ---------- comment rendering ----------


class TestRenderCommentBody:
    def test_pass_body_starts_with_marker_and_outcome(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket())
        checks = _stable_outcome()
        body = var.render_comment_body(checks, ticket, [], passed=True)
        assert body.startswith(var.COMMENT_MARKER)
        match = var.COMMENT_OUTCOME_RE.search(body)
        assert match is not None
        outcome = json.loads(match.group("outcome"))
        assert all(outcome[k] is True for k in var.CHECK_KEYS)
        assert "ai-ready-check passed" in body

    def test_fail_body_lists_each_failed_check(self, make_ticket):
        ticket = var.normalize_ticket(
            make_ticket(status="To Do", components=["wrong-component"])
        )
        checks = var.run_checklist(ticket, [])
        body = var.render_comment_body(checks, ticket, [], passed=False)
        assert "ai-ready-check failed" in body
        assert "Status is `To Do`" in body
        assert "wrong-component" in body

    def test_outcome_json_keys_are_in_check_keys_order(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket())
        checks = _stable_outcome()
        body = var.render_comment_body(checks, ticket, [], passed=True)
        match = var.COMMENT_OUTCOME_RE.search(body)
        assert match is not None
        # We ask for stable insertion order (no sort_keys), so the JSON we
        # wrote must roundtrip with keys in CHECK_KEYS order.
        text = match.group("outcome")
        positions = [text.index(f'"{k}"') for k in var.CHECK_KEYS]
        assert positions == sorted(positions), "outcome JSON keys must follow CHECK_KEYS order"

    def test_blocker_failure_lists_offending_keys(self, make_ticket):
        ticket = var.normalize_ticket(
            make_ticket(blocker_keys=["PLTPM-9000", "PLTPM-9001"])
        )
        blockers = [
            {"key": "PLTPM-9000", "status": "Done"},
            {"key": "PLTPM-9001", "status": "In Progress"},
        ]
        checks = var.run_checklist(ticket, blockers)
        body = var.render_comment_body(checks, ticket, blockers, passed=False)
        assert "PLTPM-9001" in body
        assert "In Progress" in body
        # Done blockers shouldn't show in the failure list.
        assert "PLTPM-9000`" not in body or "Done" not in body.split("Blockers not Done.")[1]


# ---------- phase 1 ----------


class TestBuildPhase1Plan:
    def test_shape(self):
        plan = var.build_phase1_plan("PLTPM-21503")
        assert plan["status"] == "needs_fetch_ticket"
        assert plan["ticket_key"] == "PLTPM-21503"
        assert len(plan["actions"]) == 1
        action = plan["actions"][0]
        assert action["tool"] == "atlassian.getJiraIssue"
        assert action["args"]["issueKey"] == "PLTPM-21503"
        assert "comments" in action["args"]["expand"]
        assert "issuelinks" in action["args"]["expand"]


# ---------- phase 2 ----------


class TestBuildPhase2Plan:
    def test_emits_one_getJiraIssue_per_blocker(self):
        plan = var.build_phase2_plan("PLTPM-1", ["PLTPM-9000", "PLTPM-9001"])
        assert plan["status"] == "needs_fetch_blockers"
        assert plan["blocker_keys"] == ["PLTPM-9000", "PLTPM-9001"]
        assert [a["args"]["issueKey"] for a in plan["actions"]] == [
            "PLTPM-9000",
            "PLTPM-9001",
        ]
        assert [a["step"] for a in plan["actions"]] == [1, 2]

    def test_preserves_order(self):
        plan = var.build_phase2_plan("PLTPM-1", ["B", "A", "C"])
        assert [a["args"]["issueKey"] for a in plan["actions"]] == ["B", "A", "C"]


# ---------- terminal plan ----------


class TestBuildTerminalPlan:
    def test_all_pass_label_missing_emits_comment_plus_label_add(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(labels=[]))
        checks = _stable_outcome()
        plan = var.build_terminal_plan(ticket, [], checks, prior_outcome=None)
        assert plan["status"] == "ai_ready"
        assert plan["outcome_changed"] is True
        assert plan["label_currently_set"] is False
        tools = [a["tool"] for a in plan["actions"]]
        assert tools == ["atlassian.addCommentToJiraIssue", "atlassian.editJiraIssue"]
        edit = plan["actions"][1]
        assert edit["args"]["update"] == {"labels": [{"add": var.AI_READY_LABEL}]}

    def test_all_pass_label_already_set_emits_only_comment(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(labels=[var.AI_READY_LABEL]))
        checks = _stable_outcome()
        plan = var.build_terminal_plan(ticket, [], checks, prior_outcome=None)
        assert plan["status"] == "ai_ready"
        # Outcome differs from None, so we still post a fresh comment, but
        # the label is already correct so no label edit.
        tools = [a["tool"] for a in plan["actions"]]
        assert tools == ["atlassian.addCommentToJiraIssue"]

    def test_no_change_when_outcome_matches_prior_and_label_state_correct(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(labels=[var.AI_READY_LABEL]))
        checks = _stable_outcome()
        plan = var.build_terminal_plan(ticket, [], checks, prior_outcome=checks)
        assert plan["status"] == "no_change"
        assert plan["actions"] == []
        assert plan["prior_outcome"] == checks

    def test_outcome_unchanged_but_label_missing_still_emits_actions(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(labels=[]))
        checks = _stable_outcome()
        plan = var.build_terminal_plan(ticket, [], checks, prior_outcome=checks)
        # Outcome matches but label is missing — must still emit actions to
        # restore the contract.
        assert plan["status"] == "ai_ready"
        tools = [a["tool"] for a in plan["actions"]]
        assert "atlassian.editJiraIssue" in tools

    def test_failure_with_label_set_emits_label_remove(self, make_ticket):
        ticket = var.normalize_ticket(
            make_ticket(status="To Do", labels=[var.AI_READY_LABEL])
        )
        checks = var.run_checklist(ticket, [])
        plan = var.build_terminal_plan(ticket, [], checks, prior_outcome=None)
        assert plan["status"] == "not_ai_ready"
        edit = plan["actions"][1]
        assert edit["args"]["update"] == {"labels": [{"remove": var.AI_READY_LABEL}]}

    def test_failure_without_label_skips_label_remove(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(status="To Do", labels=[]))
        checks = var.run_checklist(ticket, [])
        plan = var.build_terminal_plan(ticket, [], checks, prior_outcome=None)
        assert plan["status"] == "not_ai_ready"
        tools = [a["tool"] for a in plan["actions"]]
        assert tools == ["atlassian.addCommentToJiraIssue"]

    def test_failure_payload_includes_failed_items(self, make_ticket):
        ticket = var.normalize_ticket(make_ticket(status="To Do"))
        checks = var.run_checklist(ticket, [])
        plan = var.build_terminal_plan(ticket, [], checks, prior_outcome=None)
        assert "failed_items" in plan
        assert "status_next" in plan["failed_items"]


# ---------- CLI integration ----------


class TestCLI:
    def test_phase1_emits_when_only_ticket_key_provided(self, run_main):
        code, out, _err = run_main(var.main, "--ticket-key", "PLTPM-21503")
        assert code == 0
        assert out["status"] == "needs_fetch_ticket"
        assert out["ticket_key"] == "PLTPM-21503"

    def test_phase1_error_when_neither_provided(self, run_main):
        code, _out, err = run_main(var.main)
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "error"

    def test_phase2_when_blockers_present(
        self, run_main, tmp_path: Path, make_ticket
    ):
        ticket = make_ticket(blocker_keys=["PLTPM-9000", "PLTPM-9001"])
        path = tmp_path / "ticket.json"
        path.write_text(json.dumps(ticket), encoding="utf-8")
        code, out, _err = run_main(var.main, "--ticket-fixture", str(path))
        assert code == 0
        assert out["status"] == "needs_fetch_blockers"
        assert out["blocker_keys"] == ["PLTPM-9000", "PLTPM-9001"]

    def test_terminal_pass_when_ticket_clean_and_no_blockers(
        self, run_main, ticket_passes_all_fixture: Path
    ):
        code, out, _err = run_main(
            var.main, "--ticket-fixture", str(ticket_passes_all_fixture)
        )
        assert code == 0
        assert out["status"] == "ai_ready"
        assert all(out["checks"].values())

    def test_terminal_fail_when_multi_check_fail(
        self, run_main, ticket_fails_multi_fixture: Path
    ):
        code, out, _err = run_main(
            var.main, "--ticket-fixture", str(ticket_fails_multi_fixture)
        )
        assert code == 0
        assert out["status"] == "not_ai_ready"
        assert "failed_items" in out
        assert "ac_present" in out["failed_items"]
        assert "components_canonical" in out["failed_items"]
        assert "status_next" in out["failed_items"]

    def test_terminal_with_blocker_fixtures(
        self,
        run_main,
        tmp_path: Path,
        make_ticket,
        make_blocker,
    ):
        ticket = make_ticket(blocker_keys=["PLTPM-9000"])
        ticket_path = tmp_path / "ticket.json"
        ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
        blocker_path = tmp_path / "blocker.json"
        blocker_path.write_text(
            json.dumps(make_blocker(key="PLTPM-9000", status="Done")),
            encoding="utf-8",
        )
        code, out, _err = run_main(
            var.main,
            "--ticket-fixture",
            str(ticket_path),
            "--blocker-fixtures",
            str(blocker_path),
        )
        assert code == 0
        assert out["status"] == "ai_ready"
        assert out["checks"]["blockers_done"] is True

    def test_terminal_fails_when_blocker_in_progress(
        self,
        run_main,
        tmp_path: Path,
        make_ticket,
        make_blocker,
    ):
        ticket = make_ticket(blocker_keys=["PLTPM-9000"])
        ticket_path = tmp_path / "ticket.json"
        ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
        blocker_path = tmp_path / "blocker.json"
        blocker_path.write_text(
            json.dumps(make_blocker(key="PLTPM-9000", status="In Progress")),
            encoding="utf-8",
        )
        code, out, _err = run_main(
            var.main,
            "--ticket-fixture",
            str(ticket_path),
            "--blocker-fixtures",
            str(blocker_path),
        )
        assert code == 0
        assert out["status"] == "not_ai_ready"
        assert "blockers_done" in out["failed_items"]

    def test_blocker_fixture_mismatch_is_invalid_input(
        self,
        run_main,
        tmp_path: Path,
        make_ticket,
        make_blocker,
    ):
        ticket = make_ticket(blocker_keys=["PLTPM-9000"])
        ticket_path = tmp_path / "ticket.json"
        ticket_path.write_text(json.dumps(ticket), encoding="utf-8")
        wrong_blocker = tmp_path / "blocker.json"
        wrong_blocker.write_text(
            json.dumps(make_blocker(key="PLTPM-OTHER", status="Done")),
            encoding="utf-8",
        )
        code, _out, err = run_main(
            var.main,
            "--ticket-fixture",
            str(ticket_path),
            "--blocker-fixtures",
            str(wrong_blocker),
        )
        assert code == 1
        body = json.loads(err)
        assert body["status"] == "invalid_input"

    def test_ticket_key_mismatch_is_invalid_input(
        self,
        run_main,
        ticket_passes_all_fixture: Path,
    ):
        code, _out, err = run_main(
            var.main,
            "--ticket-key",
            "PLTPM-WRONG",
            "--ticket-fixture",
            str(ticket_passes_all_fixture),
        )
        assert code == 1
        body = json.loads(err)
        assert body["status"] == "invalid_input"

    def test_malformed_json_is_invalid_input(self, run_main, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        code, _out, err = run_main(var.main, "--ticket-fixture", str(path))
        assert code == 1
        body = json.loads(err)
        assert body["status"] == "invalid_input"

    def test_missing_fixture_is_invalid_input(self, run_main, tmp_path: Path):
        path = tmp_path / "does-not-exist.json"
        code, _out, err = run_main(var.main, "--ticket-fixture", str(path))
        assert code == 1
        body = json.loads(err)
        assert body["status"] == "invalid_input"


# ---------- prior-outcome integration through CLI ----------


class TestPriorOutcomeCLIIntegration:
    def test_no_change_when_prior_pass_matches_current_pass(
        self,
        run_main,
        tmp_path: Path,
        make_ticket,
    ):
        prior = _comment_with_marker(_stable_outcome())
        ticket = make_ticket(labels=[var.AI_READY_LABEL], comments=[prior])
        path = tmp_path / "ticket.json"
        path.write_text(json.dumps(ticket), encoding="utf-8")
        code, out, _err = run_main(var.main, "--ticket-fixture", str(path))
        assert code == 0
        assert out["status"] == "no_change"
        assert out["actions"] == []

    def test_outcome_change_when_prior_was_failure(
        self,
        run_main,
        tmp_path: Path,
        make_ticket,
    ):
        prior = _comment_with_marker(_stable_outcome(status_next=False))
        ticket = make_ticket(comments=[prior])
        path = tmp_path / "ticket.json"
        path.write_text(json.dumps(ticket), encoding="utf-8")
        code, out, _err = run_main(var.main, "--ticket-fixture", str(path))
        assert code == 0
        assert out["status"] == "ai_ready"
        assert out["outcome_changed"] is True


# ---------- byte-stable goldens ----------


class TestGoldens:
    def test_pass_action_plan_matches_golden(
        self, run_main, fixtures_dir: Path
    ):
        ticket = fixtures_dir / "ticket-passes-all.json"
        golden = fixtures_dir / "ai-ready-pass.expected.json"
        if not ticket.exists() or not golden.exists():
            pytest.skip("goldens not yet authored")
        code, out, _err = run_main(var.main, "--ticket-fixture", str(ticket))
        assert code == 0
        expected = json.loads(golden.read_text(encoding="utf-8"))
        assert out == expected

    def test_multi_fail_action_plan_matches_golden(
        self, run_main, fixtures_dir: Path
    ):
        ticket = fixtures_dir / "ticket-fails-multi.json"
        golden = fixtures_dir / "ai-ready-multi-fail.expected.json"
        if not ticket.exists() or not golden.exists():
            pytest.skip("goldens not yet authored")
        code, out, _err = run_main(var.main, "--ticket-fixture", str(ticket))
        assert code == 0
        expected = json.loads(golden.read_text(encoding="utf-8"))
        assert out == expected


# ---------- sentinel: keep gru-unique constants in lockstep with conventions ----------


def _live_conventions_path() -> Path:
    """Climb up to the in-repo .agents/jira-conventions.md.

    Layout: tests/ -> scripts/ -> ai-ready-check/ -> skills/ -> .agents/ -> jira-conventions.md.
    Five `.parent` hops, then the filename.
    """
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent.parent / "jira-conventions.md"


class TestSentinelAgainstConventions:
    """Pin script-level gru-unique constants against the live jira-conventions.md
    so they can't drift silently. Per locked Issue 3C, only sentinel-test
    `CANONICAL_COMPONENTS` and `AI_READY_LABEL`; trust Atlassian-stable
    globals (link types, statuses, issue types).
    """

    def test_canonical_components_match(self):
        text = _live_conventions_path().read_text(encoding="utf-8")
        for component in var.CANONICAL_COMPONENTS:
            assert (
                f"`{component}`" in text
            ), f"canonical component {component!r} missing from conventions"

    def test_ai_ready_label_documented(self):
        text = _live_conventions_path().read_text(encoding="utf-8")
        assert (
            f"`{var.AI_READY_LABEL}`" in text
        ), f"label {var.AI_READY_LABEL!r} missing from conventions"
        # And it must live under a `## Labels` section (so reviewers know
        # exactly where to look).
        assert "## Labels" in text, "conventions doc missing the `## Labels` section"
