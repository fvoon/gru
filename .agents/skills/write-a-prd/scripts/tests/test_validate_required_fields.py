"""Tests for validate_required_fields.py.

The gate has two modes:
  * `--bootstrap` -- emits an action plan instructing the agent in chat to
    fetch issue-type create-screen metadata for every gru-relevant issue
    type. The PM uses the response to populate the
    `## Required custom fields` section in jira-conventions.md.
  * `--drift-check --metadata-fixture <path>` -- compares conventions
    against a fetched fixture and reports any drift (new fields Jira
    requires that conventions don't know about, fields conventions
    declares that Jira no longer requires, or allowed-values changes).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make the script directory importable when running pytest from any cwd.
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))
_LIB_DIR = SCRIPT_DIR.parent.parent / "_lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

import required_fields as _rf  # noqa: E402

from validate_required_fields import (  # noqa: E402
    GRU_RELEVANT_ISSUE_TYPES,
    emit_bootstrap_action_plan,
    main,
    parse_metadata_fixture,
    verify_drift,
)


@pytest.fixture
def matching_required_fields():
    """Fields that match the `_matching_metadata` fixture below."""
    return _rf.parse_required_fields(
        """## Required custom fields (PLTPM)

- **`customfield_12881`** -- Activity Type
  - Default: `Engineering excellence`
  - Allowed: `New feature`, `Bug fix`, `Engineering excellence`, `Others`
  - Applies to: `Story`, `Task`
  - Wire shape: `object`

## End
"""  # noqa: E501
    )


@pytest.fixture
def matching_metadata():
    """A live Jira metadata fixture that lines up with `matching_required_fields`."""
    return {
        "Story": {
            "fields": [
                {
                    "fieldId": "customfield_12881",
                    "name": "Activity Type",
                    "required": True,
                    "allowedValues": [
                        {"value": "New feature"},
                        {"value": "Bug fix"},
                        {"value": "Engineering excellence"},
                        {"value": "Others"},
                    ],
                },
                {
                    "fieldId": "summary",
                    "name": "Summary",
                    "required": True,
                },
            ]
        },
        "Task": {
            "fields": [
                {
                    "fieldId": "customfield_12881",
                    "name": "Activity Type",
                    "required": True,
                    "allowedValues": [
                        {"value": "New feature"},
                        {"value": "Bug fix"},
                        {"value": "Engineering excellence"},
                        {"value": "Others"},
                    ],
                }
            ]
        },
    }


# ----- emit_bootstrap_action_plan -----


class TestEmitBootstrapActionPlan:
    def test_includes_one_metadata_fetch_per_issue_type(self):
        plan = emit_bootstrap_action_plan(
            project_key="PLTPM", issue_type_names=["Story", "Task"]
        )
        actions = plan["actions"]
        type_specific = [
            a
            for a in actions
            if a["tool"] == "atlassian.getJiraIssueTypeMetaWithFields"
        ]
        assert len(type_specific) == 2
        names_fetched = {a["args"]["issueTypeName"] for a in type_specific}
        assert names_fetched == {"Story", "Task"}

    def test_includes_initial_issue_types_metadata_fetch(self):
        plan = emit_bootstrap_action_plan(
            project_key="PLTPM", issue_type_names=["Story"]
        )
        first = plan["actions"][0]
        assert first["tool"] == "atlassian.getJiraProjectIssueTypesMetadata"
        assert first["args"]["projectKey"] == "PLTPM"

    def test_default_issue_types_covers_all_gru_relevant_types(self):
        plan = emit_bootstrap_action_plan(project_key="PLTPM")
        names_fetched = {
            a["args"]["issueTypeName"]
            for a in plan["actions"]
            if a["tool"] == "atlassian.getJiraIssueTypeMetaWithFields"
        }
        assert names_fetched == set(GRU_RELEVANT_ISSUE_TYPES)

    def test_status_is_needs_fetch(self):
        plan = emit_bootstrap_action_plan(project_key="PLTPM")
        assert plan["status"] == "needs_fetch"
        assert "next_step" in plan
        assert "metadata-fixture" in plan["next_step"]


# ----- parse_metadata_fixture -----


class TestParseMetadataFixture:
    def test_extracts_required_custom_fields_per_issue_type(self, matching_metadata):
        parsed = parse_metadata_fixture(matching_metadata)
        # Two issue types, each with one required customfield.
        assert set(parsed.keys()) == {"Story", "Task"}
        assert parsed["Story"]["customfield_12881"]["required"] is True
        assert parsed["Story"]["customfield_12881"]["name"] == "Activity Type"

    def test_extracts_allowed_values_as_simple_strings(self, matching_metadata):
        parsed = parse_metadata_fixture(matching_metadata)
        assert parsed["Story"]["customfield_12881"]["allowed_values"] == [
            "New feature",
            "Bug fix",
            "Engineering excellence",
            "Others",
        ]

    def test_skips_non_required_fields(self):
        meta = {
            "Story": {
                "fields": [
                    {
                        "fieldId": "customfield_99999",
                        "name": "Optional",
                        "required": False,
                    }
                ]
            }
        }
        parsed = parse_metadata_fixture(meta)
        assert parsed["Story"] == {}

    def test_skips_built_in_fields_like_summary(self, matching_metadata):
        parsed = parse_metadata_fixture(matching_metadata)
        # Built-ins (summary/description) are ignored by the helper -- we only
        # care about customfields the gru harness needs to inject.
        assert "summary" not in parsed["Story"]


# ----- verify_drift -----


class TestVerifyDrift:
    def test_matching_metadata_returns_ok(
        self, matching_required_fields, matching_metadata
    ):
        report = verify_drift(matching_required_fields, matching_metadata)
        assert report["status"] == "ok"

    def test_jira_has_new_required_field_not_in_conventions(
        self, matching_required_fields, matching_metadata
    ):
        # Add a fresh required customfield to the live metadata that the
        # conventions don't yet know about.
        matching_metadata["Story"]["fields"].append(
            {
                "fieldId": "customfield_55555",
                "name": "Region",
                "required": True,
                "allowedValues": [{"value": "US"}, {"value": "EU"}],
            }
        )
        report = verify_drift(matching_required_fields, matching_metadata)
        assert report["status"] == "drift"
        kinds = {d["kind"] for d in report["drifts"]}
        assert "new" in kinds
        new_drift = next(d for d in report["drifts"] if d["kind"] == "new")
        assert new_drift["field_id"] == "customfield_55555"
        assert new_drift["issue_type"] == "Story"

    def test_conventions_declares_field_jira_does_not_require(
        self, matching_required_fields, matching_metadata
    ):
        # Drop the customfield from Jira (no longer required there).
        matching_metadata["Story"]["fields"] = [
            f
            for f in matching_metadata["Story"]["fields"]
            if f["fieldId"] != "customfield_12881"
        ]
        report = verify_drift(matching_required_fields, matching_metadata)
        assert report["status"] == "drift"
        kinds = {d["kind"] for d in report["drifts"]}
        assert "missing" in kinds

    def test_allowed_values_changed_in_jira(
        self, matching_required_fields, matching_metadata
    ):
        # Live Jira has dropped one allowed value.
        matching_metadata["Story"]["fields"][0]["allowedValues"] = [
            {"value": "New feature"},
            {"value": "Engineering excellence"},
            {"value": "Others"},
        ]
        report = verify_drift(matching_required_fields, matching_metadata)
        assert report["status"] == "drift"
        kinds = {d["kind"] for d in report["drifts"]}
        assert "allowed_values_changed" in kinds

    def test_applies_to_drift_when_jira_requires_field_for_more_types(
        self, matching_required_fields, matching_metadata
    ):
        # Conventions says Activity Type applies to Story+Task. Suppose Jira
        # also requires it on Sub-task that conventions doesn't list.
        matching_metadata["Sub-task"] = {
            "fields": [
                {
                    "fieldId": "customfield_12881",
                    "name": "Activity Type",
                    "required": True,
                    "allowedValues": [
                        {"value": "New feature"},
                        {"value": "Bug fix"},
                        {"value": "Engineering excellence"},
                        {"value": "Others"},
                    ],
                }
            ]
        }
        report = verify_drift(matching_required_fields, matching_metadata)
        assert report["status"] == "drift"
        kinds = {d["kind"] for d in report["drifts"]}
        assert "applies_to_changed" in kinds


# ----- CLI -----


class TestCLI:
    def test_bootstrap_mode_emits_action_plan(self, run_main):
        code, out, _ = run_main(main, "--bootstrap", "--project-key", "PLTPM")
        assert code == 0
        assert out is not None
        assert out["status"] == "needs_fetch"
        assert any(
            a["tool"] == "atlassian.getJiraIssueTypeMetaWithFields"
            for a in out["actions"]
        )

    def test_drift_check_with_matching_fixture_returns_zero(
        self,
        tmp_path,
        matching_required_fields,
        matching_metadata,
        run_main,
    ):
        # Use a synthetic conventions file that lines up perfectly with the
        # `matching_metadata` fixture so the test is hermetic (no coupling to
        # the live `.agents/jira-conventions.md`).
        conv_path = tmp_path / "conventions.md"
        conv_path.write_text(
            "## Required custom fields (PLTPM)\n\n"
            "- **`customfield_12881`** -- Activity Type\n"
            "  - Default: `Engineering excellence`\n"
            "  - Allowed: `New feature`, `Bug fix`, `Engineering excellence`, `Others`\n"
            "  - Applies to: `Story`, `Task`\n"
            "  - Wire shape: `object`\n\n"
            "## End\n",
            encoding="utf-8",
        )
        fix = tmp_path / "metadata.json"
        fix.write_text(json.dumps(matching_metadata), encoding="utf-8")
        code, out, _ = run_main(
            main,
            "--drift-check",
            "--metadata-fixture",
            str(fix),
            "--conventions-path",
            str(conv_path),
        )
        assert code == 0
        assert out["status"] == "ok"

    def test_drift_check_missing_fixture_returns_two(self, tmp_path, run_main):
        missing = tmp_path / "nope.json"
        code, _, err = run_main(
            main, "--drift-check", "--metadata-fixture", str(missing)
        )
        assert code == 2
        assert err

    def test_drift_check_without_fixture_returns_two(self, run_main):
        code, _, err = run_main(main, "--drift-check")
        assert code == 2
        assert "metadata-fixture" in err
