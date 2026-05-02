"""Unit + integration tests for `validate_spike_target.py`.

Covers:
- Conventions guard (missing section, placeholder values, valid).
- Filesystem idempotency refusals (existing redline, existing worktree).
- Research-ticket parsing + lenient issue-type matching (with / without trailing space).
- Status- and comment-based idempotency refusals (Done, existing Confluence link).
- Three Confluence parent paths: elevated-parent, inline-parent, orphan.
- Each phase's action-plan shape (fetch-research, fetch-parent, fetch-confluence,
  select-repos, graphify-queries) and the terminal `ok` payload.
- Repo-list parsing (validation, dedup, order preservation).
- CLI integration via `run_main`.

Plus a sentinel test that pins canonical repos + Research issue type id against
the live `.agents/jira-conventions.md`, mirroring prd-to-jira-issues.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import validate_spike_target as vst

# ---------- conventions ----------


class TestParseConfluenceSection:
    def test_returns_all_three_keys_when_bootstrapped(self, good_conventions_path: Path):
        text = good_conventions_path.read_text(encoding="utf-8")
        out = vst.parse_confluence_section(text)
        assert out == {
            "space_key": "PLTPM",
            "space_id": "1048576",
            "spikes_parent_page_id": "9876543",
        }

    def test_raises_when_section_missing(self, missing_section_conventions_path: Path):
        text = missing_section_conventions_path.read_text(encoding="utf-8")
        with pytest.raises(vst.ConventionsError) as exc:
            vst.parse_confluence_section(text)
        assert "missing" in str(exc.value)

    def test_raises_when_values_are_placeholder(self, placeholder_conventions_path: Path):
        text = placeholder_conventions_path.read_text(encoding="utf-8")
        with pytest.raises(vst.ConventionsError) as exc:
            vst.parse_confluence_section(text)
        msg = str(exc.value)
        assert "placeholder" in msg or "not fully bootstrapped" in msg

    def test_tolerates_parenthetical_clarifications_in_keys(self):
        text = (
            "## Confluence\n"
            "- **Confluence space key** (the short letters): `PLTPM`\n"
            "- **Confluence space ID** (numeric): `42`\n"
            "- **Spikes parent page ID** (...): `99`\n"
        )
        out = vst.parse_confluence_section(text)
        assert out["space_key"] == "PLTPM"
        assert out["space_id"] == "42"
        assert out["spikes_parent_page_id"] == "99"

    def test_lists_specific_missing_keys(self, tmp_path: Path):
        text = (
            "## Confluence\n"
            "- **Confluence space key**: `PLTPM`\n"
            "- **Spikes parent page ID** (...): `99`\n"
        )
        with pytest.raises(vst.ConventionsError) as exc:
            vst.parse_confluence_section(text)
        assert "Confluence space ID" in str(exc.value)


# ---------- research-ticket normalization ----------


class TestNormalizeResearch:
    def test_extracts_summary_and_description(self, research_payload):
        out = vst.normalize_research(research_payload)
        assert out["key"] == "PLTPM-21500"
        assert "transactional outbox" in out["summary"]
        assert "wallet-to-wallet" in out["description"]

    def test_extracts_issue_type_from_dict(self, research_payload):
        out = vst.normalize_research(research_payload)
        assert out["issue_type"] == "Research "  # carries trailing space from Atlassian

    def test_status_extraction(self, research_done_payload):
        out = vst.normalize_research(research_done_payload)
        assert out["status"] == "Done"

    def test_finds_implement_parent_via_outwardIssue(self, research_payload):
        out = vst.normalize_research(research_payload)
        assert out["implement_parent_key"] == "PLTPM-21000"

    def test_no_parent_when_no_implement_link(self, research_orphan_payload):
        out = vst.normalize_research(research_orphan_payload)
        assert out["implement_parent_key"] is None

    def test_finds_implement_parent_via_inwardIssue(self):
        payload = {
            "key": "PLTPM-1",
            "fields": {
                "summary": "x",
                "description": "y",
                "issuetype": {"name": "Research "},
                "status": {"name": "To Do"},
                "issuelinks": [
                    {
                        "type": {"name": "Implement"},
                        "inwardIssue": {"key": "PLTPM-PARENT"},
                    }
                ],
            },
        }
        out = vst.normalize_research(payload)
        assert out["implement_parent_key"] == "PLTPM-PARENT"

    def test_ignores_non_implement_links(self):
        payload = {
            "key": "PLTPM-1",
            "fields": {
                "summary": "x",
                "description": "y",
                "issuetype": {"name": "Research "},
                "status": {"name": "To Do"},
                "issuelinks": [
                    {"type": {"name": "Blocks"}, "outwardIssue": {"key": "PLTPM-OTHER"}},
                ],
            },
        }
        out = vst.normalize_research(payload)
        assert out["implement_parent_key"] is None

    def test_collects_comment_bodies(self, research_with_comment_link_payload):
        out = vst.normalize_research(research_with_comment_link_payload)
        assert len(out["comments"]) == 1
        assert "Spike report posted" in out["comments"][0]

    def test_rejects_adf_description(self):
        payload = {
            "key": "PLTPM-1",
            "fields": {
                "summary": "x",
                "description": {"type": "doc", "content": []},
                "issuetype": {"name": "Research "},
                "status": {"name": "To Do"},
            },
        }
        with pytest.raises(vst.ValidationError):
            vst.normalize_research(payload)


# ---------- type / status / link helpers ----------


class TestIsResearchType:
    """Lenient on trailing whitespace (Atlassian writes `Research ` with trailing
    space; engineers may type the bare word). Case-sensitive — Jira type names
    are case-sensitive on the API side, so we keep that strict."""

    @pytest.mark.parametrize(
        "type_name",
        ["Research ", "Research", "  Research  ", "Research\t"],
    )
    def test_accepts_with_or_without_trailing_whitespace(self, type_name: str):
        assert vst.is_research_type(type_name)

    @pytest.mark.parametrize(
        "type_name",
        ["research", "research ", "RESEARCH", "Research-"],
    )
    def test_rejects_wrong_case_or_punctuation(self, type_name: str):
        assert not vst.is_research_type(type_name)

    @pytest.mark.parametrize(
        "other", ["Task", "Story", "Technical Story", "Bug", "Design", "Design "]
    )
    def test_rejects_other_types(self, other: str):
        assert not vst.is_research_type(other)


class TestIsDone:
    def test_exact(self):
        assert vst.is_done("Done")

    def test_case_insensitive(self):
        assert vst.is_done("done")

    def test_with_whitespace(self):
        assert vst.is_done(" Done ")

    @pytest.mark.parametrize("other", ["To Do", "In Progress", "next", "Cancelled"])
    def test_rejects_other_statuses(self, other: str):
        assert not vst.is_done(other)


class TestFindConfluenceInComments:
    def test_finds_url_anywhere_in_body(self):
        comments = [
            "totally unrelated",
            "see https://moneylion.atlassian.net/wiki/spaces/PLTPM/pages/123/foo for context",
        ]
        url = vst.find_confluence_in_comments(comments)
        assert url is not None
        assert "/pages/123/" in url

    def test_returns_first_match_when_multiple(self):
        comments = [
            "first https://moneylion.atlassian.net/wiki/spaces/A/pages/1/x",
            "second https://moneylion.atlassian.net/wiki/spaces/B/pages/2/y",
        ]
        url = vst.find_confluence_in_comments(comments)
        assert "/pages/1/" in url

    def test_returns_none_when_no_match(self):
        assert vst.find_confluence_in_comments(["nope", "still nothing"]) is None

    def test_handles_empty_list(self):
        assert vst.find_confluence_in_comments([]) is None


class TestIsParentElevated:
    def test_lead_in_only(self):
        elevated, url = vst.is_parent_elevated(
            {"fields": {"description": vst.ELEVATED_LEAD_IN + "\n"}}
        )
        assert elevated is True
        assert url is None

    def test_lead_in_with_url(self, parent_elevated_payload):
        elevated, url = vst.is_parent_elevated(parent_elevated_payload)
        assert elevated is True
        assert url is not None
        assert "/pages/4444/" in url

    def test_url_only(self):
        d = "See https://moneylion.atlassian.net/wiki/spaces/X/pages/77/y for details"
        elevated, url = vst.is_parent_elevated({"fields": {"description": d}})
        assert elevated is True
        assert "/pages/77/" in url

    def test_inline(self, parent_inline_payload):
        elevated, url = vst.is_parent_elevated(parent_inline_payload)
        assert elevated is False
        assert url is None


# ---------- repo selection ----------


class TestParseRepoList:
    def test_single_repo(self):
        assert vst.parse_repo_list("payment-platform") == ["payment-platform"]

    def test_multi_repo_preserves_order(self):
        assert vst.parse_repo_list("walletapi,payment-platform") == [
            "walletapi",
            "payment-platform",
        ]

    def test_strips_whitespace(self):
        assert vst.parse_repo_list(" payment-platform , walletapi ") == [
            "payment-platform",
            "walletapi",
        ]

    def test_dedups_preserving_first_occurrence(self):
        assert vst.parse_repo_list("payment-platform,walletapi,payment-platform") == [
            "payment-platform",
            "walletapi",
        ]

    def test_rejects_empty(self):
        with pytest.raises(vst.ValidationError):
            vst.parse_repo_list("")
        with pytest.raises(vst.ValidationError):
            vst.parse_repo_list(",,")

    def test_rejects_unknown(self):
        with pytest.raises(vst.ValidationError) as exc:
            vst.parse_repo_list("payment-platform,foo")
        assert "non-canonical" in str(exc.value)


# ---------- existing-redline / existing-worktree fs checks ----------


class TestExistingRedline:
    def test_returns_path_when_exists(self, tmp_path: Path):
        (tmp_path / "spike-PLTPM-1-report.md").write_text("x", encoding="utf-8")
        assert vst.existing_redline_path(tmp_path, "PLTPM-1") is not None

    def test_returns_none_when_absent(self, tmp_path: Path):
        assert vst.existing_redline_path(tmp_path, "PLTPM-1") is None

    def test_specific_filename_format(self, tmp_path: Path):
        (tmp_path / "spike-PLTPM-2-briefing.md").write_text("x", encoding="utf-8")
        # Briefing alone is not a refusal trigger (only the report file is).
        assert vst.existing_redline_path(tmp_path, "PLTPM-2") is None


class TestExistingWorktree:
    def test_returns_path_when_non_empty(self, tmp_path: Path):
        wt = tmp_path / "spike" / "PLTPM-1"
        (wt / "payment-platform").mkdir(parents=True)
        (wt / "payment-platform" / "marker").write_text("x", encoding="utf-8")
        assert vst.existing_worktree_path(tmp_path, "PLTPM-1") == wt

    def test_returns_none_when_dir_absent(self, tmp_path: Path):
        assert vst.existing_worktree_path(tmp_path, "PLTPM-1") is None

    def test_returns_none_when_dir_empty(self, tmp_path: Path):
        (tmp_path / "spike" / "PLTPM-1").mkdir(parents=True)
        assert vst.existing_worktree_path(tmp_path, "PLTPM-1") is None


# ---------- phase emitters ----------


class TestEmitters:
    def test_fetch_research_shape(self):
        plan = vst.emit_phase_fetch_research("PLTPM-99")
        assert plan["status"] == "needs_fetch_research"
        assert plan["actions"][0]["tool"] == "atlassian.getJiraIssue"
        assert plan["actions"][0]["args"]["issueKey"] == "PLTPM-99"

    def test_fetch_parent_shape(self):
        plan = vst.emit_phase_fetch_parent("PLTPM-100")
        assert plan["status"] == "needs_fetch_parent"
        assert plan["actions"][0]["args"]["issueKey"] == "PLTPM-100"

    def test_fetch_confluence_shape(self):
        plan = vst.emit_phase_fetch_confluence("https://example/wiki/spaces/X/pages/1/y")
        assert plan["status"] == "needs_fetch_confluence"
        assert plan["confluence_url"].endswith("/pages/1/y")

    def test_select_repos_shape(self):
        plan = vst.emit_phase_select_repos()
        assert plan["status"] == "needs_repo_selection"
        assert set(plan["allowed_repos"]) == set(vst.CANONICAL_REPOS)

    def test_graphify_shape_includes_repos(self, research_payload):
        research = vst.normalize_research(research_payload)
        plan = vst.emit_phase_graphify(research, ["payment-platform", "walletapi"])
        assert plan["status"] == "needs_graphify_queries"
        assert plan["scoped_repos"] == ["payment-platform", "walletapi"]
        assert plan["actions"][0]["tool"] == "graphify.search"


# ---------- CLI / run() — happy paths and refusals ----------


def _common_args(tmp_path: Path, conventions: Path) -> list[str]:
    return [
        "--research-key", "PLTPM-21500",
        "--conventions-path", str(conventions),
        "--redline-dir", str(tmp_path / "redlines-empty"),
        "--worktree-base", str(tmp_path / "worktree-base-empty"),
    ]


class TestCLIPhases:
    def test_phase1_emits_fetch_research_when_no_fixture(
        self, run_main, tmp_path: Path, good_conventions_path: Path
    ):
        code, out, _err = run_main(vst.main, *_common_args(tmp_path, good_conventions_path))
        assert code == 0
        assert out["status"] == "needs_fetch_research"

    def test_invalid_issue_type_exits_1(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
        research_wrong_type_payload,
    ):
        fixture = tmp_path / "wrong.json"
        fixture.write_text(json.dumps(research_wrong_type_payload), encoding="utf-8")
        code, _out, err = run_main(
            vst.main,
            *_common_args(tmp_path, good_conventions_path),
            "--research-fixture", str(fixture),
        )
        # Validation failures land on stderr, exit 1.
        assert code == 1
        body = json.loads(err)
        assert body["status"] == "invalid_issue_type"

    def test_done_research_refusal(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
        research_done_payload,
    ):
        fixture = tmp_path / "done.json"
        fixture.write_text(json.dumps(research_done_payload), encoding="utf-8")
        code, _out, err = run_main(
            vst.main,
            *_common_args(tmp_path, good_conventions_path),
            "--research-fixture", str(fixture),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "already_done"

    def test_existing_confluence_link_refusal(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
        research_with_comment_link_payload,
    ):
        fixture = tmp_path / "with-link.json"
        fixture.write_text(json.dumps(research_with_comment_link_payload), encoding="utf-8")
        code, _out, err = run_main(
            vst.main,
            *_common_args(tmp_path, good_conventions_path),
            "--research-fixture", str(fixture),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "existing_confluence_link"

    def test_force_skips_done_refusal(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
        research_done_payload,
    ):
        fixture = tmp_path / "done.json"
        fixture.write_text(json.dumps(research_done_payload), encoding="utf-8")
        code, out, _err = run_main(
            vst.main,
            *_common_args(tmp_path, good_conventions_path),
            "--research-fixture", str(fixture),
            "--force",
        )
        # With --force, the script proceeds past idempotency; next missing
        # fixture is the parent (since the default research has a parent link).
        assert code == 0
        assert out["status"] == "needs_fetch_parent"

    def test_existing_redline_refusal(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
    ):
        redlines = tmp_path / "redlines"
        redlines.mkdir()
        (redlines / "spike-PLTPM-21500-report.md").write_text("x", encoding="utf-8")
        code, _out, err = run_main(
            vst.main,
            "--research-key", "PLTPM-21500",
            "--conventions-path", str(good_conventions_path),
            "--redline-dir", str(redlines),
            "--worktree-base", str(tmp_path / "worktree-base"),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "existing_redline"

    def test_existing_worktree_refusal(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
    ):
        wt_base = tmp_path / "worktree-base"
        (wt_base / "spike" / "PLTPM-21500" / "payment-platform").mkdir(parents=True)
        (wt_base / "spike" / "PLTPM-21500" / "payment-platform" / "x").write_text("x")
        code, _out, err = run_main(
            vst.main,
            "--research-key", "PLTPM-21500",
            "--conventions-path", str(good_conventions_path),
            "--redline-dir", str(tmp_path / "redlines-empty"),
            "--worktree-base", str(wt_base),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "existing_worktree"

    def test_conventions_bootstrap_refusal_when_placeholder(
        self,
        run_main,
        tmp_path: Path,
        placeholder_conventions_path: Path,
    ):
        code, _out, err = run_main(
            vst.main,
            *_common_args(tmp_path, placeholder_conventions_path),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "needs_conventions_bootstrap"

    def test_conventions_bootstrap_refusal_when_section_missing(
        self,
        run_main,
        tmp_path: Path,
        missing_section_conventions_path: Path,
    ):
        code, _out, err = run_main(
            vst.main,
            *_common_args(tmp_path, missing_section_conventions_path),
        )
        assert code == 2
        body = json.loads(err)
        assert body["status"] == "needs_conventions_bootstrap"


class TestCLIElevatedParentPath:
    def test_walks_to_fetch_parent_then_confluence_then_repos_then_graphify_then_ok(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
        research_fixture: Path,
        parent_elevated_fixture: Path,
        confluence_fixture: Path,
        graphify_fixture: Path,
    ):
        common = [
            "--research-key", "PLTPM-21500",
            "--conventions-path", str(good_conventions_path),
            "--redline-dir", str(tmp_path / "redlines-empty"),
            "--worktree-base", str(tmp_path / "worktree-base-empty"),
            "--research-fixture", str(research_fixture),
        ]
        # After research fixture, expects parent fetch.
        code, out, _err = run_main(vst.main, *common)
        assert code == 0
        assert out["status"] == "needs_fetch_parent"

        # After parent fixture, expects Confluence fetch (parent is elevated).
        code, out, _err = run_main(
            vst.main, *common, "--parent-fixture", str(parent_elevated_fixture)
        )
        assert code == 0
        assert out["status"] == "needs_fetch_confluence"

        # After Confluence fixture, expects repo selection.
        code, out, _err = run_main(
            vst.main,
            *common,
            "--parent-fixture", str(parent_elevated_fixture),
            "--confluence-fixture", str(confluence_fixture),
        )
        assert code == 0
        assert out["status"] == "needs_repo_selection"

        # After repos, expects graphify queries.
        code, out, _err = run_main(
            vst.main,
            *common,
            "--parent-fixture", str(parent_elevated_fixture),
            "--confluence-fixture", str(confluence_fixture),
            "--repos", "payment-platform,walletapi",
        )
        assert code == 0
        assert out["status"] == "needs_graphify_queries"
        assert out["scoped_repos"] == ["payment-platform", "walletapi"]

        # After graphify fixture, terminal `ok`.
        code, out, _err = run_main(
            vst.main,
            *common,
            "--parent-fixture", str(parent_elevated_fixture),
            "--confluence-fixture", str(confluence_fixture),
            "--repos", "payment-platform,walletapi",
            "--graphify-fixture", str(graphify_fixture),
        )
        assert code == 0
        assert out["status"] == "ok"
        assert out["confluence_parent_path"] == "elevated-parent"
        assert out["confluence_parent_page_id"] == "4444"
        assert out["spikes_parent_page_id"] == "9876543"
        assert out["repos"] == ["payment-platform", "walletapi"]
        assert out["parent_key"] == "PLTPM-21000"


class TestCLIInlineParentPath:
    def test_inline_parent_falls_back_to_spikes_parent(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
        research_fixture: Path,
        parent_inline_fixture: Path,
        graphify_fixture: Path,
    ):
        code, out, _err = run_main(
            vst.main,
            "--research-key", "PLTPM-21500",
            "--conventions-path", str(good_conventions_path),
            "--redline-dir", str(tmp_path / "redlines-empty"),
            "--worktree-base", str(tmp_path / "worktree-base-empty"),
            "--research-fixture", str(research_fixture),
            "--parent-fixture", str(parent_inline_fixture),
            "--repos", "payment-platform",
            "--graphify-fixture", str(graphify_fixture),
        )
        assert code == 0
        assert out["status"] == "ok"
        assert out["confluence_parent_path"] == "inline-parent"
        assert out["confluence_parent_page_id"] == "9876543"
        # No Confluence fixture should have been required.


class TestCLIOrphanPath:
    def test_orphan_skips_parent_fetch_entirely(
        self,
        run_main,
        tmp_path: Path,
        good_conventions_path: Path,
        research_orphan_fixture: Path,
        graphify_fixture: Path,
    ):
        code, out, _err = run_main(
            vst.main,
            "--research-key", "PLTPM-21500",
            "--conventions-path", str(good_conventions_path),
            "--redline-dir", str(tmp_path / "redlines-empty"),
            "--worktree-base", str(tmp_path / "worktree-base-empty"),
            "--research-fixture", str(research_orphan_fixture),
            "--repos", "payment-platform",
            "--graphify-fixture", str(graphify_fixture),
        )
        assert code == 0
        assert out["status"] == "ok"
        assert out["confluence_parent_path"] == "orphan"
        assert out["parent_key"] is None
        assert out["confluence_parent_page_id"] == "9876543"


# ---------- sentinel: keep CANONICAL_REPOS + Research id in sync with conventions ----------


def _live_conventions_path() -> Path:
    """Climb up to the in-repo .agents/jira-conventions.md.

    Layout: tests/ -> scripts/ -> spike-and-report/ -> skills/ -> .agents/ -> jira-conventions.md.
    Five `.parent` hops, then the filename.
    """
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent.parent / "jira-conventions.md"


class TestSentinelAgainstConventions:
    """Pin script-level constants against the live jira-conventions.md so they
    can't drift silently. Mirrors prd-to-jira-issues' canonical-components
    sentinel."""

    def test_canonical_repos_match(self):
        text = _live_conventions_path().read_text(encoding="utf-8")
        # The canonical-components bootstrap list is a 4-item bullet group; we
        # just require each to appear with backticks. Loose match is fine —
        # the prd-to-jira-issues sentinel uses the same approach.
        for repo in vst.CANONICAL_REPOS:
            assert f"`{repo}`" in text, f"canonical repo {repo!r} missing from conventions"

    def test_research_type_id_referenced(self):
        text = _live_conventions_path().read_text(encoding="utf-8")
        # The Research type carries id 10720 in PLTPM. The script does not use
        # the id directly (it matches by name lenient-of-trailing-space), but
        # the sentinel guards against the conventions table dropping it.
        assert "`10720`" in text
        assert "Research " in text  # with the trailing space — Atlassian-side form
