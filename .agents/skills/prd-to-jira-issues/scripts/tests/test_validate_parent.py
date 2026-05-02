"""Tests for validate_parent.py.

Covers:
- elevation detection (lead-in vs URL vs neither)
- normalization (description vs Confluence body, ADF rejection)
- validation (issue type, single canonical Component, 8 sections)
- 3-phase CLI flow (no fixtures → phase 1; elevated parent only → phase 2;
  full inputs → validated parent JSON)
- malformed inputs (bad JSON, missing fields, extra components)
- canonical components stay in lockstep with conventions file
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

import validate_parent as vp

REPO_ROOT = Path(__file__).resolve().parents[5]


# ----- detection helpers -----


class TestElevationDetection:
    def test_lead_in_marks_elevated(self):
        text = (
            "**This is a significant PRD; the full text lives in Confluence.**\n\n"
            "**Confluence**: https://example/path"
        )
        assert vp.is_elevated(text) is True

    def test_confluence_url_marks_elevated(self):
        text = "See https://moneylion.atlassian.net/wiki/spaces/ENG/pages/123/Transfers"
        assert vp.is_elevated(text) is True

    def test_plain_inline_description_is_not_elevated(self):
        text = "## Problem Statement\nbla bla\n## Solution\nbla\n"
        assert vp.is_elevated(text) is False

    def test_extract_url_returns_none_for_inline(self):
        assert vp.extract_confluence_url("## Problem Statement\nbla\n") is None

    def test_extract_url_finds_canonical_shape(self):
        text = "**Confluence**: https://moneylion.atlassian.net/wiki/spaces/ENG/pages/9999/Foo"
        assert vp.extract_confluence_url(text) == (
            "https://moneylion.atlassian.net/wiki/spaces/ENG/pages/9999/Foo"
        )


# ----- normalization -----


class TestNormalize:
    def test_inline_parent_normalizes_full_prd_to_description(
        self, parent_inline_payload: dict
    ):
        n = vp.normalize_parent(parent_inline_payload, confluence_body=None)
        assert n["key"] == "PLTPM-99001"
        assert n["issue_type"] == "Story"
        assert n["components"] == ["payment-platform"]
        assert n["is_elevated"] is False
        assert n["full_prd_text"] == n["description"]
        assert n["confluence_url"] is None

    def test_elevated_parent_uses_confluence_body_when_provided(
        self, parent_elevated_payload: dict, confluence_full_body: str
    ):
        n = vp.normalize_parent(parent_elevated_payload, confluence_body=confluence_full_body)
        assert n["is_elevated"] is True
        assert n["full_prd_text"] == confluence_full_body
        assert n["confluence_url"].startswith("https://moneylion.atlassian.net")

    def test_elevated_parent_without_confluence_body_keeps_description(
        self, parent_elevated_payload: dict
    ):
        n = vp.normalize_parent(parent_elevated_payload, confluence_body=None)
        assert n["is_elevated"] is True
        assert n["full_prd_text"] == n["description"]

    def test_top_level_fields_are_tolerated(self):
        # Some MCP servers flatten fields; the script accepts both shapes.
        payload = {
            "key": "PLTPM-77",
            "summary": "Flat shape",
            "issuetype": {"name": "Story"},
            "components": [{"name": "walletapi"}],
            "description": "## Problem Statement\nx\n",
        }
        n = vp.normalize_parent(payload, confluence_body=None)
        assert n["key"] == "PLTPM-77"
        assert n["issue_type"] == "Story"
        assert n["components"] == ["walletapi"]

    def test_adf_description_is_rejected(self, parent_inline_payload: dict):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["description"] = {"type": "doc", "content": []}
        with pytest.raises(vp.InvalidParent, match="ADF"):
            vp.normalize_parent(bad, confluence_body=None)

    def test_string_components_are_accepted(self, parent_inline_payload: dict):
        # Some payloads return a list of bare strings instead of {name: ...} dicts.
        payload = copy.deepcopy(parent_inline_payload)
        payload["fields"]["components"] = ["payment-platform"]
        n = vp.normalize_parent(payload, confluence_body=None)
        assert n["components"] == ["payment-platform"]


# ----- validation -----


class TestValidate:
    def test_happy_path_inline(self, parent_inline_payload: dict):
        n = vp.normalize_parent(parent_inline_payload, confluence_body=None)
        v = vp.validate(n)
        assert v["status"] == "ok"
        assert v["primary_component"] == "payment-platform"
        assert "Problem Statement" in v["sections"]
        assert "References" in v["sections"]

    def test_happy_path_elevated_uses_confluence(
        self, parent_elevated_payload: dict, confluence_full_body: str
    ):
        n = vp.normalize_parent(parent_elevated_payload, confluence_body=confluence_full_body)
        v = vp.validate(n)
        assert v["status"] == "ok"
        assert v["primary_component"] == "payment-platform"

    def test_rejects_epic(self, parent_inline_payload: dict):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["issuetype"]["name"] = "Epic"
        n = vp.normalize_parent(bad, confluence_body=None)
        with pytest.raises(vp.InvalidParent, match="Story or Technical Story"):
            vp.validate(n)

    def test_rejects_bug(self, parent_inline_payload: dict):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["issuetype"]["name"] = "Bug"
        with pytest.raises(vp.InvalidParent):
            vp.validate(vp.normalize_parent(bad, confluence_body=None))

    def test_rejects_missing_section(self, parent_inline_payload: dict):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["description"] = bad["fields"]["description"].replace(
            "## References\nInternal slack thread.\n", ""
        )
        n = vp.normalize_parent(bad, confluence_body=None)
        with pytest.raises(vp.InvalidParent, match="References"):
            vp.validate(n)

    def test_rejects_two_canonical_components(self, parent_inline_payload: dict):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["components"] = [
            {"name": "payment-platform"},
            {"name": "walletapi"},
        ]
        n = vp.normalize_parent(bad, confluence_body=None)
        with pytest.raises(vp.InvalidParent, match="exactly one canonical Component"):
            vp.validate(n)

    def test_rejects_zero_canonical_components(self, parent_inline_payload: dict):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["components"] = [{"name": "Wallet (Transfers)"}]
        n = vp.normalize_parent(bad, confluence_body=None)
        with pytest.raises(vp.InvalidParent, match="exactly one canonical Component"):
            vp.validate(n)

    def test_aggregates_multiple_failures(self, parent_inline_payload: dict):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["issuetype"]["name"] = "Epic"
        bad["fields"]["components"] = []
        n = vp.normalize_parent(bad, confluence_body=None)
        with pytest.raises(vp.InvalidParent) as exc_info:
            vp.validate(n)
        message = str(exc_info.value)
        assert "Story or Technical Story" in message
        assert "exactly one canonical Component" in message


# ----- CLI: 3-phase flow -----


class TestPhase1:
    def test_no_parent_fixture_emits_fetch_plan(self, run_main):
        code, out, err = run_main(vp.main, "--parent-key", "PLTPM-21500")
        assert code == 0
        assert err == ""
        assert out["status"] == "needs_fetch_parent"
        assert out["actions"][0]["tool"] == "atlassian.getJiraIssue"
        assert out["actions"][0]["args"]["issueKey"] == "PLTPM-21500"


class TestPhase2:
    def test_elevated_parent_without_confluence_emits_fetch_confluence(
        self, run_main, parent_elevated_fixture: Path
    ):
        code, out, err = run_main(
            vp.main,
            "--parent-key",
            "PLTPM-99002",
            "--parent-fixture",
            str(parent_elevated_fixture),
        )
        assert code == 0
        assert err == ""
        assert out["status"] == "needs_fetch_confluence"
        assert out["actions"][0]["tool"] == "atlassian.getConfluencePage"
        assert "atlassian.net/wiki" in out["confluence_url"]


class TestPhase3:
    def test_inline_parent_validates_and_emits_normalized(
        self, run_main, parent_inline_fixture: Path
    ):
        code, out, err = run_main(
            vp.main,
            "--parent-key",
            "PLTPM-99001",
            "--parent-fixture",
            str(parent_inline_fixture),
        )
        assert code == 0
        assert err == ""
        assert out["status"] == "ok"
        assert out["primary_component"] == "payment-platform"
        assert out["issue_type"] == "Story"

    def test_elevated_parent_with_confluence_validates(
        self,
        run_main,
        parent_elevated_fixture: Path,
        confluence_fixture: Path,
    ):
        code, out, err = run_main(
            vp.main,
            "--parent-key",
            "PLTPM-99002",
            "--parent-fixture",
            str(parent_elevated_fixture),
            "--confluence-fixture",
            str(confluence_fixture),
        )
        assert code == 0
        assert err == ""
        assert out["status"] == "ok"
        assert out["is_elevated"] is True

    def test_invalid_parent_returns_code_1(
        self, capsys, parent_inline_payload: dict, tmp_path: Path
    ):
        bad = copy.deepcopy(parent_inline_payload)
        bad["fields"]["issuetype"]["name"] = "Epic"
        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        code = vp.main(
            ["--parent-key", "PLTPM-99001", "--parent-fixture", str(p)]
        )
        assert code == 1
        captured = capsys.readouterr()
        # Validation failure JSON goes to stdout (machine-readable diagnosis).
        payload = json.loads(captured.out)
        assert payload["status"] == "invalid"
        assert "Story or Technical Story" in payload["reason"]


# ----- malformed inputs -----


class TestMalformedInputs:
    def test_missing_fixture_returns_code_2(self, run_main, tmp_path: Path):
        code, _, err = run_main(
            vp.main,
            "--parent-key",
            "PLTPM-1",
            "--parent-fixture",
            str(tmp_path / "does-not-exist.json"),
        )
        assert code == 2
        assert "No such file" in err or "No such" in err or err

    def test_invalid_json_fixture_returns_code_2(self, run_main, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        code, _, err = run_main(
            vp.main, "--parent-key", "PLTPM-1", "--parent-fixture", str(bad)
        )
        assert code == 2
        assert "not valid JSON" in err

    def test_non_object_fixture_returns_code_2(self, run_main, tmp_path: Path):
        bad = tmp_path / "list.json"
        bad.write_text("[]", encoding="utf-8")
        code, _, err = run_main(
            vp.main, "--parent-key", "PLTPM-1", "--parent-fixture", str(bad)
        )
        assert code == 2
        assert "JSON object" in err

    def test_confluence_fixture_without_body_returns_code_2(
        self, run_main, parent_elevated_fixture: Path, tmp_path: Path
    ):
        bad = tmp_path / "bad_confluence.json"
        bad.write_text(json.dumps({"id": "x"}), encoding="utf-8")
        code, _, err = run_main(
            vp.main,
            "--parent-key",
            "PLTPM-99002",
            "--parent-fixture",
            str(parent_elevated_fixture),
            "--confluence-fixture",
            str(bad),
        )
        assert code == 2
        assert "body" in err


# ----- sentinel: keep CANONICAL_COMPONENTS in lockstep with conventions -----


class TestCanonicalComponentsLocked:
    def test_canonical_components_match_conventions_file(self):
        conventions_path = REPO_ROOT / ".agents" / "jira-conventions.md"
        if not conventions_path.exists():
            pytest.skip(f"conventions file not found at {conventions_path}")
        text = conventions_path.read_text(encoding="utf-8")

        # Cheap parser: pull bullets right after the "### Jira Components" header.
        section_re = re.compile(
            r"^### Jira Components.*?(?=^### |^## |\Z)", re.MULTILINE | re.DOTALL
        )
        m = section_re.search(text)
        assert m is not None, "could not find '### Jira Components' in conventions"

        bullets = re.findall(r"^- `([^`]+)`\s*$", m.group(0), re.MULTILINE)
        assert tuple(bullets) == vp.CANONICAL_COMPONENTS, (
            f"validate_parent.py CANONICAL_COMPONENTS={vp.CANONICAL_COMPONENTS!r} "
            f"is out of sync with conventions={bullets!r}; update one to match the other"
        )
