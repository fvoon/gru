"""Tests for the required-fields harness helper.

Covers the public Python API (`parse_required_fields`, `inject_required_fields`,
`FieldSpec`) and the CLI entry point. RED-first: this file imports
`required_fields` which does not exist yet -- every test should fail with
ModuleNotFoundError until the implementation lands.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import required_fields

REQUIRED_FIELDS_PATH = Path(required_fields.__file__)


# ----- conventions fixtures (inline to keep test intent visible) -----


CONVENTIONS_HAPPY_PATH = textwrap.dedent(
    """\
    # Jira conventions for gru

    ## Site

    - **Cloud ID**: `abc123`

    ## Required custom fields (PLTPM)

    > Defaults below were chosen by the PM during one-time bootstrap; override
    > per-PRD via `write_jira_prd.py --activity-type "<value>"`.

    - **`customfield_12881`** -- Activity Type
      - Default: `Engineering excellence`
      - Allowed: `New feature`, `Bug fix`, `Engineering excellence`, `Ship & Learn`, `Others`
      - Applies to: `Story`, `Technical Story`, `Task`, `Sub-task`, `Research`, `Design`
      - Wire shape: `object`

    ## Some other section

    Filler so the parser sees a section terminator.
    """
)


CONVENTIONS_TWO_FIELDS = textwrap.dedent(
    """\
    ## Required custom fields (PLTPM)

    - **`customfield_12881`** -- Activity Type
      - Default: `Engineering excellence`
      - Allowed: `Engineering excellence`, `Bug fix`
      - Applies to: `Story`, `Task`
      - Wire shape: `object`

    - **`customfield_99999`** -- Tee Shirt Size
      - Default: `M`
      - Allowed: `S`, `M`, `L`
      - Applies to: `Story`
      - Wire shape: `scalar`

    ## End
    """
)


CONVENTIONS_BOOTSTRAP_PLACEHOLDER = textwrap.dedent(
    """\
    ## Required custom fields (PLTPM)

    > <TBA -- bootstrap>: run `validate_required_fields.py --bootstrap` to
    > populate this section.

    ## End
    """
)


CONVENTIONS_NO_SECTION = textwrap.dedent(
    """\
    # Jira conventions for gru

    ## Site

    - **Cloud ID**: `abc123`

    ## Some other section

    Nothing about required fields here.
    """
)


# ----- parse_required_fields() -----


class TestParseRequiredFields:
    def test_happy_path_returns_dict_keyed_by_field_id(self):
        fields = required_fields.parse_required_fields(CONVENTIONS_HAPPY_PATH)
        assert "customfield_12881" in fields

    def test_happy_path_extracts_field_metadata(self):
        fields = required_fields.parse_required_fields(CONVENTIONS_HAPPY_PATH)
        spec = fields["customfield_12881"]
        assert spec.field_id == "customfield_12881"
        assert spec.display_name == "Activity Type"
        assert spec.default_value == "Engineering excellence"
        assert "Engineering excellence" in spec.allowed_values
        assert "Ship & Learn" in spec.allowed_values
        assert "Story" in spec.applies_to
        assert "Sub-task" in spec.applies_to
        assert spec.wire_shape == "object"

    def test_multiple_fields_all_parsed(self):
        fields = required_fields.parse_required_fields(CONVENTIONS_TWO_FIELDS)
        assert set(fields.keys()) == {"customfield_12881", "customfield_99999"}
        assert fields["customfield_99999"].display_name == "Tee Shirt Size"
        assert fields["customfield_99999"].wire_shape == "scalar"

    def test_section_missing_raises_with_helpful_message(self):
        with pytest.raises(ValueError, match="Required custom fields"):
            required_fields.parse_required_fields(CONVENTIONS_NO_SECTION)

    def test_bootstrap_placeholder_raises_with_pointer(self):
        with pytest.raises(ValueError, match="bootstrap"):
            required_fields.parse_required_fields(CONVENTIONS_BOOTSTRAP_PLACEHOLDER)

    def test_default_must_be_in_allowed_values(self):
        bad = textwrap.dedent(
            """\
            ## Required custom fields (PLTPM)

            - **`customfield_111`** -- Bad
              - Default: `Z`
              - Allowed: `A`, `B`, `C`
              - Applies to: `Story`
              - Wire shape: `object`

            ## End
            """
        )
        with pytest.raises(ValueError, match="default.*not in allowed"):
            required_fields.parse_required_fields(bad)

    def test_unknown_wire_shape_raises(self):
        bad = textwrap.dedent(
            """\
            ## Required custom fields (PLTPM)

            - **`customfield_111`** -- Bad
              - Default: `A`
              - Allowed: `A`, `B`
              - Applies to: `Story`
              - Wire shape: `weird`

            ## End
            """
        )
        with pytest.raises(ValueError, match="wire shape"):
            required_fields.parse_required_fields(bad)


# ----- inject_required_fields() -----


class TestInjectRequiredFields:
    def setup_method(self):
        self.fields = required_fields.parse_required_fields(CONVENTIONS_HAPPY_PATH)

    def test_injects_default_for_applicable_issue_type(self):
        args = {"projectKey": "PLTPM", "issueType": "Story", "summary": "x"}
        out = required_fields.inject_required_fields(args, self.fields, "Story")
        assert out["additional_fields"]["customfield_12881"] == {
            "value": "Engineering excellence"
        }

    def test_does_not_mutate_input_dict(self):
        args = {"projectKey": "PLTPM", "issueType": "Story", "summary": "x"}
        before = json.dumps(args, sort_keys=True)
        required_fields.inject_required_fields(args, self.fields, "Story")
        after = json.dumps(args, sort_keys=True)
        assert before == after, "inject_required_fields must not mutate caller's args"

    def test_skips_field_when_issue_type_not_in_applies_to(self):
        # Activity Type applies to Story etc., but not to (say) Epic.
        args = {"projectKey": "PLTPM", "issueType": "Epic", "summary": "x"}
        out = required_fields.inject_required_fields(args, self.fields, "Epic")
        assert "additional_fields" not in out

    def test_uses_override_when_provided(self):
        args = {"projectKey": "PLTPM", "issueType": "Story", "summary": "x"}
        out = required_fields.inject_required_fields(
            args, self.fields, "Story", overrides={"customfield_12881": "Bug fix"}
        )
        assert out["additional_fields"]["customfield_12881"] == {"value": "Bug fix"}

    def test_override_value_must_be_in_allowed_list(self):
        args = {"projectKey": "PLTPM", "issueType": "Story", "summary": "x"}
        with pytest.raises(ValueError, match="not in allowed"):
            required_fields.inject_required_fields(
                args,
                self.fields,
                "Story",
                overrides={"customfield_12881": "Tornado"},
            )

    def test_preserves_existing_additional_fields(self):
        args = {
            "projectKey": "PLTPM",
            "issueType": "Story",
            "summary": "x",
            "additional_fields": {"customfield_77777": "preexisting"},
        }
        out = required_fields.inject_required_fields(args, self.fields, "Story")
        assert out["additional_fields"]["customfield_77777"] == "preexisting"
        assert out["additional_fields"]["customfield_12881"] == {
            "value": "Engineering excellence"
        }

    def test_does_not_clobber_explicit_existing_value_for_same_field(self):
        args = {
            "projectKey": "PLTPM",
            "issueType": "Story",
            "summary": "x",
            "additional_fields": {"customfield_12881": {"value": "Customer excellence"}},
        }
        out = required_fields.inject_required_fields(args, self.fields, "Story")
        # Caller-supplied value wins; helper does not overwrite.
        assert out["additional_fields"]["customfield_12881"] == {
            "value": "Customer excellence"
        }

    def test_scalar_wire_shape_emits_string_value(self):
        fields = required_fields.parse_required_fields(CONVENTIONS_TWO_FIELDS)
        args = {"projectKey": "PLTPM", "issueType": "Story", "summary": "x"}
        out = required_fields.inject_required_fields(args, fields, "Story")
        assert out["additional_fields"]["customfield_99999"] == "M"
        # And the object-shaped one is still object-shaped.
        assert out["additional_fields"]["customfield_12881"] == {
            "value": "Engineering excellence"
        }

    def test_empty_fields_dict_is_a_noop(self):
        args = {"projectKey": "PLTPM", "issueType": "Story", "summary": "x"}
        out = required_fields.inject_required_fields(args, {}, "Story")
        assert out == args
        assert "additional_fields" not in out

    def test_unknown_override_field_id_raises(self):
        args = {"projectKey": "PLTPM", "issueType": "Story", "summary": "x"}
        with pytest.raises(ValueError, match="unknown.*field"):
            required_fields.inject_required_fields(
                args,
                self.fields,
                "Story",
                overrides={"customfield_does_not_exist": "x"},
            )


# ----- CLI dispatcher (used for ad-hoc inspection) -----


class TestCLI:
    def test_parse_subcommand_outputs_json(self, tmp_path):
        conv = tmp_path / "conventions.md"
        conv.write_text(CONVENTIONS_HAPPY_PATH)
        result = subprocess.run(
            [
                sys.executable,
                str(REQUIRED_FIELDS_PATH),
                "parse",
                "--conventions-path",
                str(conv),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        assert "customfield_12881" in payload
        assert payload["customfield_12881"]["default_value"] == "Engineering excellence"

    def test_parse_with_missing_section_exits_nonzero(self, tmp_path):
        conv = tmp_path / "conventions.md"
        conv.write_text(CONVENTIONS_NO_SECTION)
        result = subprocess.run(
            [
                sys.executable,
                str(REQUIRED_FIELDS_PATH),
                "parse",
                "--conventions-path",
                str(conv),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert "Required custom fields" in result.stderr
