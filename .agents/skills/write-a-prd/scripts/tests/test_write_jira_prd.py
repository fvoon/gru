"""Unit tests for write_jira_prd.py.

Covers parent-type heuristic, Component derivation, requires_user_choice path,
description composition, summary truncation, mode plumbing, golden plans, and
idempotency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
# Make `_lib/` importable so the test seam `import required_fields` works without
# relying on `write_jira_prd`'s side-effect-laden sys.path patching.
_LIB_DIR = SCRIPT_DIR.parent.parent / "_lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

import required_fields as _rf  # noqa: E402

from write_jira_prd import (  # noqa: E402
    _LIB_PATH,
    CANONICAL_COMPONENTS,
    CONFLUENCE_PLACEHOLDER,
    REQUIRED_SECTIONS,
    SUMMARY_HARD_LIMIT,
    SUMMARY_SOFT_LIMIT,
    InvalidDraft,
    compose_inline_description,
    detect_components,
    detect_parent_type,
    detect_user_story_actors,
    extract_h1_title,
    is_system_actor,
    main,
    plan,
    truncate_summary,
)

_TEST_REQUIRED_FIELDS = _rf.parse_required_fields(
    """## Required custom fields (PLTPM)

- **`customfield_12881`** -- Activity Type
  - Default: `Engineering excellence`
  - Allowed: `New feature`, `Bug fix`, `Customer excellence`, `Engineering excellence`, `Ship & Learn`, `Others`
  - Applies to: `Story`, `Technical Story`, `Task`, `Sub-task`, `Research `, `Design `
  - Wire shape: `object`

## End
"""  # noqa: E501
)

# ---------- module bootstrap ----------


class TestLibPathResolution:
    """Regression tests for the F1 friction-3 path bug.

    `_LIB_PATH` must resolve to the actual `.agents/skills/_lib` directory
    so a direct `python write_jira_prd.py` invocation can import
    `markdown.py` without a PYTHONPATH workaround. Pytest happens to mask
    the bug because `significance_check.py` (which derives `_LIB_PATH`
    correctly) is usually imported first into the same process and adds
    the right path to `sys.path`.
    """

    def test_lib_path_points_to_an_existing_directory(self):
        assert _LIB_PATH.is_dir(), (
            f"_LIB_PATH {_LIB_PATH!r} does not exist; check the .parent count "
            f"in write_jira_prd.py"
        )

    def test_lib_path_contains_markdown_module(self):
        assert (_LIB_PATH / "markdown.py").is_file(), (
            f"_LIB_PATH {_LIB_PATH!r} exists but is missing markdown.py"
        )

    def test_lib_path_resolves_to_canonical_skills_lib_dir(self):
        assert _LIB_PATH.name == "_lib"
        assert _LIB_PATH.parent.name == "skills"


# ---------- helpers ----------


def _draft(
    title: str = "Sample PRD",
    stories: list[str] | None = None,
    cross_app: list[str] | None = None,
    extras: dict[str, str] | None = None,
    elevate_marker: bool = False,
) -> str:
    """Build a synthetic draft with the 8 required sections + optional H1 / marker."""
    stories = stories or ["1. As a customer, I want X, so that Y."]
    cross_app = cross_app or ["- payment-platform: change ProcessorHealthClient."]
    extras = extras or {}

    parts = []
    if title:
        parts.append(f"# {title}\n")
    if elevate_marker:
        parts.append("<!-- elevate-to-confluence -->\n")
    for section in REQUIRED_SECTIONS:
        parts.append(f"## {section}")
        if section == "User Stories":
            parts.extend(stories)
        elif section == "Cross-Application Impact":
            parts.extend(cross_app)
        elif section in extras:
            parts.append(extras[section])
        else:
            parts.append(f"Body for {section}.")
        parts.append("")
    return "\n".join(parts)


# ---------- extract_h1_title ----------


def test_h1_title_returns_first_header():
    assert extract_h1_title("# Hello\n\nbody\n") == "Hello"


def test_h1_title_strips_trailing_whitespace():
    assert extract_h1_title("#   Spaced Title   \n") == "Spaced Title"


def test_h1_title_empty_when_missing():
    assert extract_h1_title("## H2 only\n") == ""


# ---------- actor detection ----------


def test_actors_with_articles():
    body = (
        "1. As a customer, I want X.\n"
        "2. As an engineer, I want Y.\n"
        "3. As the merchant ops user, I want Z.\n"
    )
    assert detect_user_story_actors(body) == ["customer", "engineer", "merchant ops"]


def test_actors_without_articles():
    body = "1. As payment-platform, I want X.\n2. As TransferSaga, I want Y.\n"
    assert detect_user_story_actors(body) == ["payment-platform", "TransferSaga"]


def test_actors_skip_non_story_lines():
    body = "Random prose. As a stowaway, I want.\n1. As a real, I want.\n"
    assert detect_user_story_actors(body) == ["real"]


def test_is_system_actor_kebab_case_true():
    assert is_system_actor("payment-platform") is True
    assert is_system_actor("transfer-saga") is True


def test_is_system_actor_camel_case_true():
    assert is_system_actor("TransferSaga") is True
    assert is_system_actor("SQS consumer") is True


def test_is_system_actor_plain_lowercase_false():
    assert is_system_actor("customer") is False
    assert is_system_actor("merchant ops") is False
    assert is_system_actor("engineer") is False  # ambiguous; defaults to user-facing


def test_is_system_actor_first_letter_capital_only_is_user_facing():
    """A leading capital (e.g. proper noun) without internal caps stays user-facing."""
    assert is_system_actor("Merchant") is False


# ---------- detect_parent_type ----------


def test_parent_type_user_facing_story():
    body = "1. As a customer, I want X.\n2. As a merchant, I want Y.\n"
    assert detect_parent_type(body) == "Story"


def test_parent_type_all_system_technical_story():
    body = "1. As payment-platform, I want X.\n2. As TransferSaga, I want Y.\n"
    assert detect_parent_type(body) == "Technical Story"


def test_parent_type_mixed_defaults_to_story():
    """If any actor is user-facing, the parent is a Story (per heuristic)."""
    body = "1. As payment-platform, I want X.\n2. As a customer, I want Y.\n"
    assert detect_parent_type(body) == "Story"


def test_parent_type_no_as_stories_defaults_to_story():
    body = "1. We need to add X.\n2. We need to add Y.\n"
    assert detect_parent_type(body) == "Story"


# ---------- detect_components ----------


def test_components_single_canonical():
    body = "- payment-platform: do something long enough to be real."
    assert detect_components(body) == ["payment-platform"]


def test_components_multiple_canonical_returned_in_canonical_order():
    body = (
        "- walletapi: change A.\n"
        "- payment-platform: change B.\n"
        "- spring-boot-starters: bump C.\n"
    )
    # Expected order matches CANONICAL_COMPONENTS, not draft order.
    assert detect_components(body) == [
        "payment-platform",
        "walletapi",
        "spring-boot-starters",
    ]


def test_components_ignores_non_canonical_repos():
    body = (
        "- payment-platform: ok.\n"
        "- some-other-repo: also touched.\n"
        "- (etc.)\n"
    )
    assert detect_components(body) == ["payment-platform"]


def test_components_empty_when_no_canonical_referenced():
    body = "- some-other-repo: nope.\n- (etc.)"
    assert detect_components(body) == []


# ---------- truncate_summary ----------


def test_truncate_summary_under_hard_limit_unchanged():
    s = "x" * 200
    assert truncate_summary(s) == s


def test_truncate_summary_at_hard_limit_unchanged():
    s = "x" * SUMMARY_HARD_LIMIT
    assert truncate_summary(s) == s


def test_truncate_summary_over_hard_limit_truncated_with_ellipsis():
    s = "x" * (SUMMARY_HARD_LIMIT + 50)
    out = truncate_summary(s)
    assert len(out) == SUMMARY_HARD_LIMIT
    assert out.endswith("...")


# ---------- compose_inline_description ----------


def test_compose_inline_strips_h1_and_marker():
    text = "# Title\n\n<!-- elevate-to-confluence -->\n\n## Problem Statement\nbody.\n"
    out = compose_inline_description(text, {})
    assert "# Title" not in out
    assert "<!-- elevate-to-confluence -->" not in out
    assert "## Problem Statement" in out


# ---------- plan: inline path ----------


def test_plan_inline_single_repo_one_action():
    draft_text = _draft()
    result = plan(draft_text, mode="inline")
    assert result["mode"] == "inline"
    assert result["issue_type"] == "Story"
    assert result["primary_component"] == "payment-platform"
    assert result["candidate_components"] == ["payment-platform"]
    assert result["requires_user_choice"] is None
    assert len(result["actions"]) == 1
    action = result["actions"][0]
    assert action["tool"] == "atlassian.createJiraIssue"
    assert action["args"]["issueType"] == "Story"
    assert action["args"]["components"] == ["payment-platform"]
    assert action["args"]["projectKey"] == "PLTPM"


def test_plan_inline_multi_repo_emits_requires_user_choice():
    draft_text = _draft(
        cross_app=[
            "- payment-platform: change A.",
            "- walletapi: change B.",
        ],
    )
    result = plan(draft_text, mode="inline")
    assert result["primary_component"] is None
    assert result["candidate_components"] == ["payment-platform", "walletapi"]
    assert result["requires_user_choice"] is not None
    assert result["requires_user_choice"]["options"] == [
        "payment-platform",
        "walletapi",
    ]
    assert result["actions"] == []


def test_plan_inline_description_renders_all_required_sections_in_order():
    draft_text = _draft()
    result = plan(draft_text, mode="inline")
    description = result["actions"][0]["args"]["description"]
    last = -1
    for section in REQUIRED_SECTIONS:
        idx = description.find(f"## {section}")
        assert idx != -1, f"missing section: {section}"
        assert idx > last, f"section out of order: {section}"
        last = idx


def test_plan_system_only_actors_yields_technical_story():
    draft_text = _draft(
        stories=[
            "1. As payment-platform, I want to do X.",
            "2. As TransferSaga, I want to do Y.",
        ]
    )
    result = plan(draft_text, mode="inline")
    assert result["issue_type"] == "Technical Story"


def test_plan_user_facing_actors_yield_story():
    draft_text = _draft(
        stories=[
            "1. As a customer, I want to do X.",
            "2. As a merchant, I want to do Y.",
        ]
    )
    result = plan(draft_text, mode="inline")
    assert result["issue_type"] == "Story"


# ---------- plan: elevate path ----------


def test_plan_elevate_three_actions():
    draft_text = _draft(elevate_marker=True)
    result = plan(draft_text, mode="elevate")
    assert result["mode"] == "elevate"
    assert len(result["actions"]) == 3
    assert result["actions"][0]["tool"] == "atlassian.createConfluencePage"
    assert result["actions"][1]["tool"] == "atlassian.createJiraIssue"
    assert result["actions"][2]["tool"] == "atlassian.addConfluenceRemoteLinkToJiraIssue"


def test_plan_elevate_jira_description_references_confluence_placeholder():
    draft_text = _draft()
    result = plan(draft_text, mode="elevate")
    jira_action = result["actions"][1]
    assert CONFLUENCE_PLACEHOLDER in jira_action["args"]["description"]
    assert jira_action["substitutions"] == {
        CONFLUENCE_PLACEHOLDER: "{{confluence_page_url}}"
    }


def test_plan_elevate_confluence_page_strips_h1_and_marker():
    draft_text = _draft(elevate_marker=True)
    result = plan(draft_text, mode="elevate")
    body = result["actions"][0]["args"]["body"]
    assert "# Sample PRD" not in body
    assert "<!-- elevate-to-confluence -->" not in body
    assert "## Problem Statement" in body


def test_plan_elevate_confluence_title_prefixed_with_PRD():
    draft_text = _draft(title="My Big Feature")
    result = plan(draft_text, mode="elevate")
    assert result["actions"][0]["args"]["title"] == "PRD: My Big Feature"


def test_plan_elevate_remote_link_uses_substitution_placeholders():
    draft_text = _draft()
    result = plan(draft_text, mode="elevate")
    link = result["actions"][2]
    assert link["args"]["issueKey"] == "{{parent_issue_key}}"
    assert link["args"]["url"] == "{{confluence_page_url}}"


# ---------- plan: errors ----------


def test_plan_missing_h1_raises():
    draft_text = _draft(title="").replace("# \n", "")
    with pytest.raises(InvalidDraft, match="H1"):
        plan(draft_text, mode="inline")


def test_plan_missing_required_section_raises():
    draft_text = _draft()
    draft_text = draft_text.replace("## Out of Scope\nBody for Out of Scope.\n", "")
    with pytest.raises(InvalidDraft, match="Out of Scope"):
        plan(draft_text, mode="inline")


def test_plan_no_canonical_repos_raises():
    draft_text = _draft(cross_app=["- some-other-repo: change A."])
    with pytest.raises(InvalidDraft, match="canonical repo"):
        plan(draft_text, mode="inline")


def test_plan_unknown_mode_raises():
    draft_text = _draft()
    with pytest.raises(InvalidDraft, match="unknown mode"):
        plan(draft_text, mode="weird")


# ---------- summary clamp + flag ----------


def test_plan_summary_under_soft_limit_flag_false():
    draft_text = _draft(title="Short title")
    result = plan(draft_text, mode="inline")
    assert result["summary"] == "Short title"
    assert result["summary_exceeds_soft_limit"] is False


def test_plan_summary_over_soft_limit_under_hard_kept_with_flag():
    long_title = "x" * (SUMMARY_SOFT_LIMIT + 10)
    draft_text = _draft(title=long_title)
    result = plan(draft_text, mode="inline")
    assert result["summary"] == long_title
    assert result["summary_exceeds_soft_limit"] is True


def test_plan_summary_over_hard_limit_truncated():
    long_title = "x" * (SUMMARY_HARD_LIMIT + 50)
    draft_text = _draft(title=long_title)
    result = plan(draft_text, mode="inline")
    assert len(result["summary"]) == SUMMARY_HARD_LIMIT
    assert result["summary"].endswith("...")


# ---------- idempotency ----------


def test_plan_inline_is_idempotent():
    draft_text = _draft()
    a = plan(draft_text, mode="inline")
    b = plan(draft_text, mode="inline")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_plan_elevate_is_idempotent():
    draft_text = _draft(elevate_marker=True)
    a = plan(draft_text, mode="elevate")
    b = plan(draft_text, mode="elevate")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------- golden tests against fixture drafts ----------


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture_pair(slug: str) -> tuple[str, dict]:
    draft = (FIXTURES_DIR / f"{slug}.md").read_text(encoding="utf-8")
    expected = json.loads(
        (FIXTURES_DIR / f"{slug}.expected.json").read_text(encoding="utf-8")
    )
    return draft, expected


def test_golden_inline_merchant_health():
    draft, expected = _load_fixture_pair("inline-merchant-health")
    actual = plan(draft, mode="inline")
    assert actual == expected


def test_golden_elevate_transfers_v3():
    draft, expected = _load_fixture_pair("elevate-transfers-v3")
    actual = plan(draft, mode="elevate")
    assert actual == expected


# ---------- main (CLI; uses shared `run_main` fixture from conftest.py) ----------


def test_main_with_mode_flag(tmp_path, run_main):
    draft = tmp_path / "draft.md"
    draft.write_text(_draft(), encoding="utf-8")
    code, result, _ = run_main(main, "--draft", str(draft), "--mode", "inline")
    assert code == 0
    assert result["mode"] == "inline"


def test_main_with_decision_json(tmp_path, run_main):
    draft = tmp_path / "draft.md"
    draft.write_text(_draft(), encoding="utf-8")
    decision = tmp_path / "decision.json"
    decision.write_text('{"decision": "elevate", "reasons": []}', encoding="utf-8")
    code, result, _ = run_main(
        main,
        "--draft", str(draft),
        "--decision-json", str(decision),
    )
    assert code == 0
    assert result["mode"] == "elevate"


def test_main_decision_json_overrides_mode_when_consistent(tmp_path, run_main):
    draft = tmp_path / "draft.md"
    draft.write_text(_draft(), encoding="utf-8")
    decision = tmp_path / "decision.json"
    decision.write_text('{"decision": "inline"}', encoding="utf-8")
    code, _, _ = run_main(
        main,
        "--draft", str(draft),
        "--mode", "inline",
        "--decision-json", str(decision),
    )
    assert code == 0


def test_main_decision_json_conflicts_with_mode(tmp_path, run_main):
    draft = tmp_path / "draft.md"
    draft.write_text(_draft(), encoding="utf-8")
    decision = tmp_path / "decision.json"
    decision.write_text('{"decision": "elevate"}', encoding="utf-8")
    code, _, err = run_main(
        main,
        "--draft", str(draft),
        "--mode", "inline",
        "--decision-json", str(decision),
    )
    assert code == 2
    assert "conflicts" in err


def test_main_missing_mode_and_decision(tmp_path, run_main):
    draft = tmp_path / "draft.md"
    draft.write_text(_draft(), encoding="utf-8")
    code, _, err = run_main(main, "--draft", str(draft))
    assert code == 2
    assert "decision-json" in err or "mode" in err


def test_main_missing_draft_returns_two(tmp_path, run_main):
    code, _, _ = run_main(
        main, "--draft", str(tmp_path / "nope.md"), "--mode", "inline"
    )
    assert code == 2


def test_main_malformed_draft_returns_two(tmp_path, run_main):
    draft = tmp_path / "bad.md"
    draft.write_text("# header\n\nbut no sections\n", encoding="utf-8")
    code, _, err = run_main(main, "--draft", str(draft), "--mode", "inline")
    assert code == 2
    assert "missing" in err


# ---------- canonical components matches conventions ----------


def test_canonical_components_locked():
    """Sentinel: the script's CANONICAL_COMPONENTS must match jira-conventions.md."""
    from validate_components import find_conventions, parse_canonical_components
    conventions = find_conventions(SCRIPT_DIR).read_text(encoding="utf-8")
    from_conventions = parse_canonical_components(conventions)
    assert list(CANONICAL_COMPONENTS) == from_conventions


# ---------- required custom fields injection (F1 friction 6/10) ----------


class TestRequiredFieldsInjection:
    """The plan() output must inject every customfield in the conventions
    `## Required custom fields` section into the args of every
    `atlassian.createJiraIssue` action -- otherwise the agent in chat
    eats an HTTP 400 per attempt.
    """

    def _create_issue_actions(self, result: dict) -> list[dict]:
        return [a for a in result["actions"] if a["tool"] == "atlassian.createJiraIssue"]

    def test_inline_path_injects_default_activity_type(self):
        result = plan(_draft(), mode="inline", required_fields=_TEST_REQUIRED_FIELDS)
        creates = self._create_issue_actions(result)
        assert len(creates) == 1
        assert creates[0]["args"]["additional_fields"] == {
            "customfield_12881": {"value": "Engineering excellence"}
        }

    def test_elevate_path_injects_default_activity_type_on_jira_action(self):
        result = plan(
            _draft(elevate_marker=True),
            mode="elevate",
            required_fields=_TEST_REQUIRED_FIELDS,
        )
        creates = self._create_issue_actions(result)
        assert len(creates) == 1
        assert creates[0]["args"]["additional_fields"] == {
            "customfield_12881": {"value": "Engineering excellence"}
        }

    def test_does_not_inject_into_confluence_action(self):
        """createConfluencePage and addConfluenceRemoteLinkToJiraIssue must not gain
        `additional_fields` (they aren't Jira-issue creates)."""
        result = plan(
            _draft(elevate_marker=True),
            mode="elevate",
            required_fields=_TEST_REQUIRED_FIELDS,
        )
        for action in result["actions"]:
            if action["tool"] != "atlassian.createJiraIssue":
                assert "additional_fields" not in action["args"]

    def test_override_uses_chosen_value(self):
        result = plan(
            _draft(),
            mode="inline",
            required_fields=_TEST_REQUIRED_FIELDS,
            activity_type_override="Customer excellence",
        )
        creates = self._create_issue_actions(result)
        assert creates[0]["args"]["additional_fields"] == {
            "customfield_12881": {"value": "Customer excellence"}
        }

    def test_invalid_override_value_raises(self):
        with pytest.raises(InvalidDraft, match="not in allowed"):
            plan(
                _draft(),
                mode="inline",
                required_fields=_TEST_REQUIRED_FIELDS,
                activity_type_override="Tornado",
            )

    def test_empty_required_fields_dict_is_a_noop(self):
        """Passing an empty fields dict (e.g. tests that don't care about injection)
        should not add `additional_fields` to any action."""
        result = plan(_draft(), mode="inline", required_fields={})
        creates = self._create_issue_actions(result)
        assert "additional_fields" not in creates[0]["args"]

    def test_default_loads_from_conventions_when_required_fields_not_passed(self):
        """When the caller doesn't supply required_fields, plan() must parse
        the live `.agents/jira-conventions.md` file. End-to-end check that the
        wire-up actually reads from disk."""
        result = plan(_draft(), mode="inline")
        creates = self._create_issue_actions(result)
        assert "additional_fields" in creates[0]["args"]
        assert "customfield_12881" in creates[0]["args"]["additional_fields"]

    def test_plan_remains_idempotent_after_injection(self):
        a = plan(_draft(), mode="inline", required_fields=_TEST_REQUIRED_FIELDS)
        b = plan(_draft(), mode="inline", required_fields=_TEST_REQUIRED_FIELDS)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


class TestActivityTypeFlagInMain:
    def test_main_with_activity_type_flag_propagates_to_action_plan(self, tmp_path, run_main):
        draft = tmp_path / "draft.md"
        draft.write_text(_draft(), encoding="utf-8")
        code, result, _ = run_main(
            main,
            "--draft", str(draft),
            "--mode", "inline",
            "--activity-type", "New feature",
        )
        assert code == 0
        creates = [a for a in result["actions"] if a["tool"] == "atlassian.createJiraIssue"]
        assert creates[0]["args"]["additional_fields"] == {
            "customfield_12881": {"value": "New feature"}
        }

    def test_main_with_invalid_activity_type_returns_two(self, tmp_path, run_main):
        draft = tmp_path / "draft.md"
        draft.write_text(_draft(), encoding="utf-8")
        code, _, err = run_main(
            main,
            "--draft", str(draft),
            "--mode", "inline",
            "--activity-type", "Tornado",
        )
        assert code == 2
        assert "not in allowed" in err
