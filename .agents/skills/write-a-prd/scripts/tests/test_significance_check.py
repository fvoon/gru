"""Unit tests for significance_check.py.

Each branch of the heuristic is exercised in isolation, at the boundary, and in combination.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from significance_check import (  # noqa: E402
    ELEVATE_MARKER,
    MalformedDraft,
    count_non_trivial_cross_app_bullets,
    count_numbered_items,
    main,
    max_mermaid_block_lines,
    score,
    split_top_level_sections,
)

# ----- helpers -----


def _draft(stories: int = 1, xapp_entries: int = 1, xapp_long: bool = True,
           mermaid_lines: int = 0, marker: bool = False) -> str:
    """Build a synthetic draft with controllable knobs."""
    parts = [
        "## Problem Statement", "Some problem.\n",
        "## Solution", "Some solution.\n",
        "## User Stories",
    ]
    for i in range(1, stories + 1):
        parts.append(f"{i}. As a user, I want feature {i}, so that benefit {i}.")
    parts.append("")
    parts.append("## Cross-Application Impact")
    for i in range(xapp_entries):
        if xapp_long:
            parts.append(f"- repo-{i}: " + ("x" * 60))
        else:
            parts.append(f"- repo-{i}: short")
    parts.append("")
    parts.append("## Implementation Decisions")
    parts.append("- something\n")
    parts.append("## Out of Scope")
    parts.append("Nope.\n")
    parts.append("## Further Notes")
    parts.append("None.\n")
    parts.append("## References")
    parts.append("- (none)\n")
    if mermaid_lines > 0:
        parts.append("```mermaid")
        for j in range(mermaid_lines):
            parts.append(f"line_{j} --> line_{j+1}")
        parts.append("```")
        parts.append("")
    if marker:
        parts.append(ELEVATE_MARKER)
    return "\n".join(parts)


# ----- split_top_level_sections -----


def test_split_returns_each_h2_as_key():
    text = "# Title\n\nIntro.\n\n## A\n\na body\n\n## B\n\nb body\n"
    sections = split_top_level_sections(text)
    assert "A" in sections and "B" in sections
    assert "a body" in sections["A"]
    assert "b body" in sections["B"]


def test_split_ignores_h1_and_h3():
    text = "# Top\n\n## H2\n\n### Sub\n\nfoo\n\n## H2-2\n"
    sections = split_top_level_sections(text)
    assert "H2" in sections
    assert "Sub" not in sections


# ----- count_numbered_items -----


def test_count_numbered_items_counts_top_level_only():
    body = (
        "1. one\n"
        "2. two\n"
        "    a. nested\n"
        "    b. nested\n"
        "3. three\n"
    )
    assert count_numbered_items(body) == 3


def test_count_numbered_items_handles_double_digits():
    body = "\n".join(f"{i}. story {i}" for i in range(1, 12))
    assert count_numbered_items(body) == 11


def test_count_numbered_items_zero_for_unrelated_text():
    assert count_numbered_items("Just prose, no numbers.") == 0


# ----- count_non_trivial_cross_app_bullets -----


def test_cross_app_counts_only_long_descriptions():
    body = (
        "- payment-platform: " + ("a" * 60) + "\n"
        "- walletapi: short\n"
        "- infrastructure: " + ("b" * 51) + "\n"
        "- spring-boot-starters: " + ("c" * 49) + "\n"
        "- (etc.)\n"
    )
    assert count_non_trivial_cross_app_bullets(body) == 2


def test_cross_app_threshold_is_strictly_greater():
    body = "- repo: " + ("x" * 50)
    assert count_non_trivial_cross_app_bullets(body) == 0
    body = "- repo: " + ("x" * 51)
    assert count_non_trivial_cross_app_bullets(body) == 1


def test_cross_app_skips_bullets_without_colon():
    body = (
        "- payment-platform: " + ("a" * 60) + "\n"
        "- a long bullet without a repo prefix that happens to be longer than fifty characters\n"
    )
    assert count_non_trivial_cross_app_bullets(body) == 1


# ----- max_mermaid_block_lines -----


def test_mermaid_returns_zero_when_absent():
    assert max_mermaid_block_lines("no mermaid here") == 0


def test_mermaid_counts_inner_lines():
    text = "```mermaid\nA\nB\nC\n```\n"
    assert max_mermaid_block_lines(text) == 3


def test_mermaid_returns_largest_block():
    text = (
        "```mermaid\nA\nB\n```\n\n"
        "```mermaid\nA\nB\nC\nD\nE\n```\n"
    )
    assert max_mermaid_block_lines(text) == 5


def test_mermaid_ignores_other_languages():
    text = "```python\nfor i in range(50):\n    print(i)\n```\n"
    assert max_mermaid_block_lines(text) == 0


# ----- score: each branch in isolation -----


def test_score_inline_minimal():
    text = _draft(stories=1, xapp_entries=1)
    result = score(text)
    assert result["decision"] == "inline"
    assert result["reasons"] == []


def test_score_elevates_on_eleven_user_stories():
    text = _draft(stories=11)
    result = score(text)
    assert result["decision"] == "elevate"
    assert any("user stories" in r for r in result["reasons"])
    assert result["metrics"]["user_stories"] == 11


def test_score_inline_at_exactly_ten_user_stories():
    """Boundary: exactly 10 stories -> stay inline (rule is strictly > 10)."""
    text = _draft(stories=10)
    result = score(text)
    assert result["decision"] == "inline"


def test_score_elevates_on_four_long_cross_app_entries():
    text = _draft(xapp_entries=4, xapp_long=True)
    result = score(text)
    assert result["decision"] == "elevate"
    assert any("Cross-Application Impact" in r for r in result["reasons"])


def test_score_inline_at_exactly_three_cross_app_entries():
    text = _draft(xapp_entries=3, xapp_long=True)
    result = score(text)
    assert result["decision"] == "inline"


def test_score_inline_when_cross_app_entries_are_short():
    """Many bullets but all short -> none counted as non-trivial -> inline."""
    text = _draft(xapp_entries=10, xapp_long=False)
    result = score(text)
    assert result["decision"] == "inline"
    assert result["metrics"]["cross_app_impact_entries"] == 0


def test_score_elevates_on_thirty_one_line_mermaid():
    text = _draft(mermaid_lines=31)
    result = score(text)
    assert result["decision"] == "elevate"
    assert any("mermaid" in r for r in result["reasons"])


def test_score_inline_at_exactly_thirty_line_mermaid():
    text = _draft(mermaid_lines=30)
    result = score(text)
    assert result["decision"] == "inline"


def test_score_elevates_on_explicit_marker():
    text = _draft(marker=True)
    result = score(text)
    assert result["decision"] == "elevate"
    assert any("marker" in r for r in result["reasons"])
    assert result["metrics"]["has_elevate_marker"] is True


def test_score_combines_multiple_reasons():
    text = _draft(stories=11, xapp_entries=4, mermaid_lines=31, marker=True)
    result = score(text)
    assert result["decision"] == "elevate"
    assert len(result["reasons"]) == 4


def test_score_raises_when_user_stories_section_missing():
    text = (
        "## Problem Statement\nx\n\n## Solution\ny\n\n## Cross-Application Impact\n- repo: ok\n"
    )
    with pytest.raises(MalformedDraft, match="User Stories"):
        score(text)


def test_score_raises_when_cross_app_section_missing():
    text = (
        "## Problem Statement\nx\n\n## Solution\ny\n\n## User Stories\n1. As a user...\n"
    )
    with pytest.raises(MalformedDraft, match="Cross-Application Impact"):
        score(text)


def test_score_raises_on_empty_draft():
    with pytest.raises(MalformedDraft):
        score("")


# ----- main (CLI; uses shared `run_main` fixture from conftest.py) -----


def test_main_inline_path(tmp_path, run_main):
    draft = tmp_path / "draft.md"
    draft.write_text(_draft(), encoding="utf-8")
    code, result, _ = run_main(main, "--draft", str(draft))
    assert code == 0
    assert result["decision"] == "inline"


def test_main_elevate_path(tmp_path, run_main):
    draft = tmp_path / "draft.md"
    draft.write_text(_draft(stories=20), encoding="utf-8")
    code, result, _ = run_main(main, "--draft", str(draft))
    assert code == 0
    assert result["decision"] == "elevate"


def test_main_missing_file_returns_two(tmp_path, run_main):
    code, _, err = run_main(main, "--draft", str(tmp_path / "nope.md"))
    assert code == 2
    assert "error" in err


def test_main_malformed_returns_two(tmp_path, run_main):
    draft = tmp_path / "bad.md"
    draft.write_text("# header only\n", encoding="utf-8")
    code, _, err = run_main(main, "--draft", str(draft))
    assert code == 2
    assert "malformed" in err
