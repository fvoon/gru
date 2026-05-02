"""Unit + integration tests for `write_spike_report.py`.

Covers:
- Section parsing: 7 required sections detected; missing-section refusals.
- TODO-guarded validation: refuses publish if any TODO-guarded section still
  contains the literal `TODO` token; auto-populated sections (Existing state,
  Spike metadata) are exempt.
- Action-plan ordering: 3 phases — createConfluencePage / addCommentToJiraIssue
  / transitionJiraIssue — in that order.
- Page-title derivation from the first line of the Question body.
- `--update-existing` flow: emits updateConfluencePage instead of create;
  refuses if --existing-page-id missing.
- Markdown -> Confluence storage-format converter:
  - h2 / h3 headings
  - bullet lists
  - fenced code blocks (with + without language)
  - inline `code`, `[label](url)` links
  - paragraphs
  - special-character escaping
- Elapsed-minutes computation: within budget / exceeded / unparseable
  `started_at`.
- State-file errors: missing keys, non-JSON, missing file.
- CLI: happy path, refusals, default state-fixture lookup, --now injection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import write_spike_report as wsr

FIXED_NOW_WITHIN = "2026-05-02T18:18:00+00:00"  # 18 minutes after 18:00 UTC.
FIXED_NOW_OVER = "2026-05-02T19:00:00+00:00"  # 60 minutes after.
FIXED_NOW_BAD = "not-a-date"


# ---------- section parsing ----------


class TestParseReport:
    def test_returns_all_seven_sections(self, redlined_report_text: str):
        parsed = wsr.parse_report(redlined_report_text)
        assert set(parsed) == set(wsr.REQUIRED_SECTIONS)

    def test_question_body_contains_summary(self, redlined_report_text: str):
        parsed = wsr.parse_report(redlined_report_text)
        assert "transactional outbox" in parsed["Question"]

    def test_missing_section_raises(self, redlined_report_text: str):
        truncated = redlined_report_text.replace("## Spike metadata", "## REPLACED")
        with pytest.raises(wsr.InvalidReport) as exc:
            wsr.parse_report(truncated)
        assert "Spike metadata" in str(exc.value)

    def test_missing_multiple_sections_lists_all(self, redlined_report_text: str):
        truncated = redlined_report_text.replace(
            "## Spike metadata", "## REPLACED1"
        ).replace("## Suggested follow-up tickets", "## REPLACED2")
        with pytest.raises(wsr.InvalidReport) as exc:
            wsr.parse_report(truncated)
        msg = str(exc.value)
        assert "Spike metadata" in msg
        assert "Suggested follow-up tickets" in msg


# ---------- TODO guard ----------


class TestFindTodoViolations:
    def test_clean_report_has_none(self, redlined_report_text: str):
        parsed = wsr.parse_report(redlined_report_text)
        assert wsr.find_todo_violations(parsed) == []

    def test_detects_todo_in_what_was_tried(self, redlined_report_text: str):
        text = redlined_report_text.replace(
            "Approach A: rely on Axon", "TODO finish writing this section"
        )
        parsed = wsr.parse_report(text)
        offenders = wsr.find_todo_violations(parsed)
        assert "What was tried" in offenders

    def test_ignores_todo_in_auto_populated_sections(self, redlined_report_text: str):
        # The skeleton stamps "TODO" into nothing, but if the engineer
        # accidentally leaves "TODO" in section 7's auto-populated stub, we
        # still don't fail — section 7 is overwritten anyway.
        text = redlined_report_text.replace(
            "_(auto-populated by write_spike_report.py at publish time.)_",
            "TODO leftover from skeleton",
        )
        parsed = wsr.parse_report(text)
        offenders = wsr.find_todo_violations(parsed)
        assert offenders == []

    def test_detects_word_boundary_only(self, redlined_report_text: str):
        # `TODOMORROW` should NOT trigger; we want the exact `TODO` word.
        text = redlined_report_text.replace(
            "Approach B adds", "TODOMORROW we will revisit this. Approach B adds"
        )
        parsed = wsr.parse_report(text)
        assert wsr.find_todo_violations(parsed) == []

    def test_lists_multiple_offenders(self, redlined_report_text: str):
        text = redlined_report_text.replace(
            "Approach A: rely on Axon", "TODO 1"
        ).replace("Approach B — a transactional outbox", "TODO 2")
        parsed = wsr.parse_report(text)
        offenders = wsr.find_todo_violations(parsed)
        assert "What was tried" in offenders
        assert "Recommended approach" in offenders


# ---------- state file ----------


class TestLoadState:
    def test_happy_path(self, state_fixture: Path):
        state = wsr.load_state(state_fixture)
        assert state["research_key"] == "PLTPM-21500"

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(wsr.StateFileError) as exc:
            wsr.load_state(tmp_path / "no-such.json")
        assert "failed to read" in str(exc.value)

    def test_invalid_json(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(wsr.StateFileError) as exc:
            wsr.load_state(bad)
        assert "not valid JSON" in str(exc.value)

    def test_missing_keys(self, tmp_path: Path):
        bad = tmp_path / "missing.json"
        bad.write_text(json.dumps({"research_key": "X"}), encoding="utf-8")
        with pytest.raises(wsr.StateFileError) as exc:
            wsr.load_state(bad)
        assert "missing keys" in str(exc.value)

    def test_non_object_root(self, tmp_path: Path):
        bad = tmp_path / "list.json"
        bad.write_text("[]", encoding="utf-8")
        with pytest.raises(wsr.StateFileError):
            wsr.load_state(bad)


# ---------- elapsed minutes ----------


class TestComputeElapsedMinutes:
    def test_within_budget(self):
        from datetime import datetime, timezone
        now = datetime.fromisoformat(FIXED_NOW_WITHIN)
        assert wsr.compute_elapsed_minutes("2026-05-02T18:00:00+00:00", now) == 18
        del timezone  # keep linter quiet about unused

    def test_over_budget(self):
        from datetime import datetime
        now = datetime.fromisoformat(FIXED_NOW_OVER)
        assert wsr.compute_elapsed_minutes("2026-05-02T18:00:00+00:00", now) == 60

    def test_unparseable_returns_neg1(self):
        assert wsr.compute_elapsed_minutes("not-a-date") == -1

    def test_naive_started_at_treated_as_utc(self):
        from datetime import datetime
        now = datetime.fromisoformat(FIXED_NOW_WITHIN)
        # Naive input — script should defensively coerce to UTC.
        assert wsr.compute_elapsed_minutes("2026-05-02T18:00:00", now) == 18

    def test_clock_skew_negative_floors_to_zero(self):
        from datetime import datetime
        now = datetime.fromisoformat("2026-05-02T17:00:00+00:00")  # before start.
        assert wsr.compute_elapsed_minutes("2026-05-02T18:00:00+00:00", now) == 0


# ---------- markdown -> Confluence storage ----------


class TestMarkdownToConfluence:
    def test_h2_heading(self):
        out = wsr.markdown_to_confluence_storage("## Title")
        assert out == "<h2>Title</h2>"

    def test_h3_heading(self):
        out = wsr.markdown_to_confluence_storage("### Subtitle")
        assert out == "<h3>Subtitle</h3>"

    def test_paragraph(self):
        out = wsr.markdown_to_confluence_storage("hello world")
        assert out == "<p>hello world</p>"

    def test_paragraph_joins_consecutive_lines(self):
        out = wsr.markdown_to_confluence_storage("line one\nline two")
        assert out == "<p>line one line two</p>"

    def test_bullet_list(self):
        out = wsr.markdown_to_confluence_storage("- alpha\n- beta")
        assert out == "<ul><li>alpha</li><li>beta</li></ul>"

    def test_bullet_list_with_dash_star_plus(self):
        out = wsr.markdown_to_confluence_storage("- a\n* b\n+ c")
        assert out == "<ul><li>a</li><li>b</li><li>c</li></ul>"

    def test_inline_code(self):
        out = wsr.markdown_to_confluence_storage("see `WalletPublisher` here")
        assert "<code>WalletPublisher</code>" in out

    def test_link(self):
        out = wsr.markdown_to_confluence_storage("see [docs](https://x.com/y)")
        assert '<a href="https://x.com/y">docs</a>' in out

    def test_fenced_code_block_with_language(self):
        out = wsr.markdown_to_confluence_storage("```java\nint x = 1;\n```")
        assert 'ac:name="code"' in out
        assert 'ac:name="language">java' in out
        assert "int x = 1;" in out

    def test_fenced_code_block_without_language(self):
        out = wsr.markdown_to_confluence_storage("```\nplain code\n```")
        assert 'ac:name="code"' in out
        assert "plain code" in out
        assert "language" not in out  # no language parameter emitted.

    def test_fenced_block_content_not_processed_as_markdown(self):
        # The `## ` inside the fence must NOT become an `<h2>`.
        out = wsr.markdown_to_confluence_storage("```\n## not a heading\n```")
        assert "<h2>" not in out
        assert "## not a heading" in out

    def test_special_chars_escaped_in_paragraph(self):
        out = wsr.markdown_to_confluence_storage("a < b & c > d")
        assert "&lt;" in out and "&amp;" in out and "&gt;" in out

    def test_special_chars_escaped_in_inline_code(self):
        out = wsr.markdown_to_confluence_storage("see `a<b>c`")
        assert "<code>a&lt;b&gt;c</code>" in out

    def test_blockquote(self):
        out = wsr.markdown_to_confluence_storage("> draft\n> warning")
        assert out == "<blockquote>draft warning</blockquote>"

    def test_blank_line_separates_paragraphs(self):
        out = wsr.markdown_to_confluence_storage("first\n\nsecond")
        assert out.count("<p>") == 2


# ---------- page title ----------


class TestPageTitle:
    def test_uses_first_line_of_question_body(self, redlined_report_text: str):
        parsed = wsr.parse_report(redlined_report_text)
        # The Question body's first line is `**Spike: do we need ...?**`
        title = wsr.page_title(state={"research_key": "X"}, parsed=parsed)
        assert title.startswith("Spike: ")
        assert "transactional outbox" in title

    def test_falls_back_when_question_empty(self):
        parsed = {"Question": ""}
        title = wsr.page_title({"research_key": "PLTPM-21500"}, parsed)
        assert title == "Spike: PLTPM-21500"

    def test_strips_emphasis_markers_and_dedupes_spike_prefix(self):
        # Skeleton convention: first line is `**Spike: foo bar?**`. Page
        # title should NOT double the prefix.
        parsed = {"Question": "**Spike: foo bar?**"}
        title = wsr.page_title({"research_key": "X"}, parsed)
        assert title == "Spike: foo bar?"

    def test_no_spike_prefix_in_first_line_still_works(self):
        parsed = {"Question": "**Investigate the thing**"}
        title = wsr.page_title({"research_key": "X"}, parsed)
        assert title == "Spike: Investigate the thing"


# ---------- action plan ----------


class TestActionPlan:
    def _build(
        self,
        redlined_report_text: str,
        state_payload: dict,
        update_existing: bool = False,
        existing_page_id: str | None = None,
    ) -> dict:
        parsed = wsr.parse_report(redlined_report_text)
        return wsr.build_action_plan(
            state=state_payload,
            parsed=parsed,
            graphify_summary=None,
            elapsed_minutes=18,
            update_existing=update_existing,
            existing_page_id=existing_page_id,
        )

    def test_three_phase_ordering(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        assert [a["phase"] for a in plan["actions"]] == ["publish", "comment", "transition"]

    def test_steps_are_1_2_3(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        assert [a["step"] for a in plan["actions"]] == [1, 2, 3]

    def test_create_uses_parent_id_from_state(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        create = plan["actions"][0]
        assert create["tool"] == "atlassian.createConfluencePage"
        assert create["args"]["parentPageId"] == "4444"

    def test_create_uses_space_id_from_state(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        assert plan["actions"][0]["args"]["spaceId"] == "1048576"

    def test_storage_format_is_confluence_xhtml(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        body = plan["actions"][0]["args"]["body"]["storage"]
        assert body["representation"] == "storage"
        assert "<h2>" in body["value"]
        assert 'ac:name="code"' in body["value"]

    def test_comment_carries_research_key(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        comment = plan["actions"][1]
        assert comment["args"]["issueKey"] == "PLTPM-21500"
        assert "{{confluence_page_url}}" in comment["args"]["body"]

    def test_transition_targets_done(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        trans = plan["actions"][2]
        assert trans["tool"] == "atlassian.transitionJiraIssue"
        assert trans["args"]["transition"] == {"name": "Done"}

    def test_elapsed_in_envelope(self, redlined_report_text, state_payload):
        plan = self._build(redlined_report_text, state_payload)
        assert plan["elapsed_minutes"] == 18
        assert plan["budget_minutes"] == wsr.WALL_CLOCK_BUDGET_MINUTES

    def test_update_existing_emits_update_tool(self, redlined_report_text, state_payload):
        plan = self._build(
            redlined_report_text, state_payload,
            update_existing=True, existing_page_id="111222",
        )
        assert plan["actions"][0]["tool"] == "atlassian.updateConfluencePage"
        assert plan["actions"][0]["args"]["pageId"] == "111222"

    def test_update_existing_without_page_id_raises(
        self, redlined_report_text, state_payload
    ):
        with pytest.raises(wsr.InvalidReport) as exc:
            self._build(
                redlined_report_text, state_payload,
                update_existing=True, existing_page_id=None,
            )
        assert "existing-page-id" in str(exc.value)


# ---------- metadata footer ----------


class TestMetadataSection:
    def test_within_budget_says_within(self, state_payload):
        body = wsr.render_metadata_section(
            state_payload, elapsed_minutes=18, repos=["payment-platform"], branch="spike/X"
        )
        assert "within budget" in body
        assert "18 / 30 min" in body

    def test_over_budget_says_exceeded(self, state_payload):
        body = wsr.render_metadata_section(
            state_payload, elapsed_minutes=60, repos=["payment-platform"], branch="spike/X"
        )
        assert "BUDGET_EXCEEDED" in body

    def test_orphan_parent_renders_as_none(self, state_payload):
        state_payload["parent_key"] = None
        body = wsr.render_metadata_section(
            state_payload, elapsed_minutes=18, repos=["x"], branch="spike/X"
        )
        assert "orphan spike" in body

    def test_unknown_elapsed_renders_unknown(self, state_payload):
        body = wsr.render_metadata_section(
            state_payload, elapsed_minutes=-1, repos=["x"], branch="spike/X"
        )
        assert "unknown" in body


# ---------- CLI ----------


class TestCLI:
    def test_happy_path(
        self, run_main, redlined_report_fixture: Path, state_fixture: Path
    ):
        code, out, _err = run_main(
            wsr.main,
            "--report", str(redlined_report_fixture),
            "--state-fixture", str(state_fixture),
            "--now", FIXED_NOW_WITHIN,
        )
        assert code == 0
        assert out["status"] == "ok"
        assert out["elapsed_minutes"] == 18
        assert [a["phase"] for a in out["actions"]] == ["publish", "comment", "transition"]

    def test_todo_remaining_refusal(
        self, run_main, tmp_path: Path, redlined_report_text: str, state_fixture: Path
    ):
        bad = tmp_path / "bad.md"
        bad.write_text(
            redlined_report_text.replace("Approach A: rely on Axon", "TODO unfinished"),
            encoding="utf-8",
        )
        code, _out, err = run_main(
            wsr.main,
            "--report", str(bad),
            "--state-fixture", str(state_fixture),
        )
        assert code == 1
        body = json.loads(err)
        assert body["status"] == "todo_remaining"
        assert "What was tried" in body["sections"]

    def test_missing_section_refusal(
        self, run_main, tmp_path: Path, redlined_report_text: str, state_fixture: Path
    ):
        bad = tmp_path / "bad.md"
        bad.write_text(
            redlined_report_text.replace("## Spike metadata", "## REPLACED"),
            encoding="utf-8",
        )
        code, _out, err = run_main(
            wsr.main,
            "--report", str(bad),
            "--state-fixture", str(state_fixture),
        )
        assert code == 1
        body = json.loads(err)
        assert body["status"] == "invalid_report"

    def test_state_error_exits_2(
        self, run_main, redlined_report_fixture: Path, tmp_path: Path
    ):
        code, _out, err = run_main(
            wsr.main,
            "--report", str(redlined_report_fixture),
            "--state-fixture", str(tmp_path / "no-such-state.json"),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "state_error"

    def test_bad_now_flag_exits_2(
        self, run_main, redlined_report_fixture: Path, state_fixture: Path
    ):
        code, _out, err = run_main(
            wsr.main,
            "--report", str(redlined_report_fixture),
            "--state-fixture", str(state_fixture),
            "--now", FIXED_NOW_BAD,
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "error"

    def test_action_plan_matches_golden(self, run_main):
        """Byte-stable contract: the canonical 'transactional outbox'
        scenario emits exactly the checked-in action-plan JSON.

        Re-run via:
          .venv/bin/python write_spike_report.py \\
            --report tests/fixtures/report-outbox-redlined.md \\
            --state-fixture tests/fixtures/state-outbox.json \\
            --now '2026-05-02T18:18:00+00:00' \\
            > tests/fixtures/outbox-write-action-plan.expected.json
        """
        fixtures = Path(__file__).parent / "fixtures"
        code, out, _err = run_main(
            wsr.main,
            "--report", str(fixtures / "report-outbox-redlined.md"),
            "--state-fixture", str(fixtures / "state-outbox.json"),
            "--now", "2026-05-02T18:18:00+00:00",
        )
        assert code == 0
        expected = json.loads(
            (fixtures / "outbox-write-action-plan.expected.json").read_text(encoding="utf-8")
        )
        assert out == expected, "action plan diverged from golden — re-run generator if intended"

    def test_default_state_fixture_path_for_named_report(self):
        # `redlines/spike-PLTPM-21500-report.md`
        # -> `~/IdeaProjects/spike/PLTPM-21500/.spike-state.json`
        out = wsr._default_state_fixture_for(Path("redlines/spike-PLTPM-21500-report.md"))
        assert "PLTPM-21500" in str(out)
        assert str(out).endswith(".spike-state.json")
