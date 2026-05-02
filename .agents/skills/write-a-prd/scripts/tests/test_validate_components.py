"""Unit tests for validate_components.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the script directory importable when running pytest from any cwd.
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from validate_components import (  # noqa: E402
    emit_action_plan,
    main,
    parse_canonical_components,
    verify,
)

# ----- parse_canonical_components -----


def test_parse_canonical_returns_four_in_order(conventions_text):
    assert parse_canonical_components(conventions_text) == [
        "payment-platform",
        "walletapi",
        "infrastructure",
        "spring-boot-starters",
    ]


def test_parse_canonical_raises_when_section_missing():
    text = "# Jira conventions\n\n## Some other section\n\n- `unrelated`\n"
    with pytest.raises(ValueError, match="could not find '### Jira Components'"):
        parse_canonical_components(text)


def test_parse_canonical_raises_when_section_has_no_bullets():
    text = "### Jira Components — 4 to create\n\nNo bullets here.\n\n### Next\n"
    with pytest.raises(ValueError, match="no `name` bullet items"):
        parse_canonical_components(text)


def test_parse_canonical_stops_at_first_non_bullet_after_block():
    """Subsequent backtick text outside the bullet block must not be picked up."""
    text = (
        "### Jira Components — 4 to create\n"
        "\n"
        "Intro paragraph.\n"
        "\n"
        "- `payment-platform`\n"
        "- `walletapi`\n"
        "- `infrastructure`\n"
        "- `spring-boot-starters`\n"
        "\n"
        "**Note**: `something-else` should not be captured.\n"
        "\n"
        "- `also-not-this`\n"
        "\n"
        "### Next section\n"
    )
    assert parse_canonical_components(text) == [
        "payment-platform",
        "walletapi",
        "infrastructure",
        "spring-boot-starters",
    ]


# ----- verify -----


def test_verify_all_present_returns_ok():
    canonical = ["a", "b"]
    fixture = [{"name": "a"}, {"name": "b"}, {"name": "extra"}]
    assert verify(canonical, fixture) == {"status": "ok", "canonical": ["a", "b"]}


def test_verify_some_missing_returns_missing_list():
    canonical = ["a", "b", "c"]
    fixture = [{"name": "a"}]
    result = verify(canonical, fixture)
    assert result["status"] == "missing"
    assert result["missing"] == ["b", "c"]
    assert result["canonical"] == canonical
    assert result["fetched"] == ["a"]
    assert "remediation" in result


def test_verify_all_missing_lists_all():
    canonical = ["a", "b"]
    fixture = [{"name": "x"}]
    result = verify(canonical, fixture)
    assert result["status"] == "missing"
    assert result["missing"] == ["a", "b"]


def test_verify_handles_malformed_entries():
    """Entries without a 'name' key (or non-dict) must be ignored, not crash."""
    canonical = ["a"]
    fixture = [{"name": "a"}, {"id": "no_name"}, "not_a_dict", None]
    assert verify(canonical, fixture)["status"] == "ok"


# ----- emit_action_plan -----


def test_action_plan_lists_canonical_and_one_action():
    plan = emit_action_plan(["a", "b"])
    assert plan["status"] == "needs_fetch"
    assert plan["canonical"] == ["a", "b"]
    assert len(plan["actions"]) == 1
    assert plan["actions"][0]["tool"] == "atlassian.getJiraProjectComponents"
    assert plan["actions"][0]["args"] == {"projectKey": "PLTPM"}


# ----- main (CLI integration; uses shared `run_main` fixture from conftest.py) -----


def test_main_default_mode_emits_action_plan(conventions_file, run_main):
    code, plan, _ = run_main(main, "--conventions-path", str(conventions_file))
    assert code == 0
    assert plan["status"] == "needs_fetch"
    assert plan["canonical"] == [
        "payment-platform",
        "walletapi",
        "infrastructure",
        "spring-boot-starters",
    ]


def test_main_fixture_all_present_returns_zero(
    conventions_file, components_fixture_all_present, run_main
):
    code, result, _ = run_main(
        main,
        "--conventions-path", str(conventions_file),
        "--components-fixture", str(components_fixture_all_present),
    )
    assert code == 0
    assert result["status"] == "ok"


def test_main_fixture_some_missing_returns_one(
    conventions_file, components_fixture_some_missing, run_main
):
    code, result, _ = run_main(
        main,
        "--conventions-path", str(conventions_file),
        "--components-fixture", str(components_fixture_some_missing),
    )
    assert code == 1
    assert result["status"] == "missing"
    assert set(result["missing"]) == {"walletapi", "infrastructure"}


def test_main_fixture_all_missing_returns_one(
    conventions_file, components_fixture_all_missing, run_main
):
    code, result, _ = run_main(
        main,
        "--conventions-path", str(conventions_file),
        "--components-fixture", str(components_fixture_all_missing),
    )
    assert code == 1
    assert result["status"] == "missing"
    assert len(result["missing"]) == 4


def test_main_missing_conventions_returns_two(tmp_path, run_main):
    code, _, err = run_main(main, "--conventions-path", str(tmp_path / "nope.md"))
    assert code == 2
    assert "error" in err


def test_main_malformed_conventions_returns_two(tmp_path, run_main):
    bad = tmp_path / "bad.md"
    bad.write_text("# Just a header, no Jira Components section.\n", encoding="utf-8")
    code, _, err = run_main(main, "--conventions-path", str(bad))
    assert code == 2
    assert "Jira Components" in err


def test_main_invalid_fixture_json_returns_two(conventions_file, tmp_path, run_main):
    bad_fixture = tmp_path / "bad.json"
    bad_fixture.write_text("not json {", encoding="utf-8")
    code, _, err = run_main(
        main,
        "--conventions-path", str(conventions_file),
        "--components-fixture", str(bad_fixture),
    )
    assert code == 2
    assert "valid JSON" in err


def test_main_fixture_not_list_returns_two(conventions_file, tmp_path, run_main):
    bad_fixture = tmp_path / "obj.json"
    bad_fixture.write_text('{"name":"a"}', encoding="utf-8")
    code, _, err = run_main(
        main,
        "--conventions-path", str(conventions_file),
        "--components-fixture", str(bad_fixture),
    )
    assert code == 2
    assert "list" in err
