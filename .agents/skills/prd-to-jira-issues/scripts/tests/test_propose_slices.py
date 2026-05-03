"""Tests for propose_slices.py.

Covers each heuristic in isolation (cross-app bullets, weak components, ordering,
research/design detection, AC stub) plus end-to-end CLI flow + idempotency
(byte-stable output) + redline-file overwrite refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import propose_slices as ps

# ----- helpers -----


def _normalized_parent(*, sections: dict[str, str], summary: str = "Test parent") -> dict:
    """Build a parent JSON shaped like validate_parent.py's output."""
    body = "\n\n".join(f"## {k}\n{v.strip()}" for k, v in sections.items())
    return {
        "key": "PLTPM-99001",
        "summary": summary,
        "issue_type": "Story",
        "components": ["payment-platform"],
        "primary_component": "payment-platform",
        "is_elevated": False,
        "confluence_url": None,
        "description": body,
        "full_prd_text": body,
        "sections": sections,
    }


# ----- bullet parsing -----


class TestParseCrossAppBullets:
    def test_single_repo_bullet(self):
        body = "- payment-platform: extend ProcessorHealthClient."
        assert ps.parse_cross_app_bullets(body) == [
            ("payment-platform", "extend ProcessorHealthClient.")
        ]

    def test_multi_repo_bullets_in_order(self):
        body = (
            "- payment-platform: orchestrator changes.\n"
            "- walletapi: ledger split.\n"
            "- infrastructure: SQS queue.\n"
        )
        result = ps.parse_cross_app_bullets(body)
        assert [r[0] for r in result] == ["payment-platform", "walletapi", "infrastructure"]

    def test_drops_non_canonical_repos(self):
        body = (
            "- payment-platform: yes.\n"
            "- some-third-party-service: no.\n"
            "- walletapi: yes.\n"
        )
        result = ps.parse_cross_app_bullets(body)
        assert [r[0] for r in result] == ["payment-platform", "walletapi"]

    def test_empty_section_yields_no_bullets(self):
        assert ps.parse_cross_app_bullets("") == []

    @pytest.mark.parametrize("marker", ["-", "*", "+"])
    def test_accepts_all_commonmark_bullet_markers(self, marker):
        body = f"{marker} payment-platform: extend ProcessorHealthClient."
        assert ps.parse_cross_app_bullets(body) == [
            ("payment-platform", "extend ProcessorHealthClient.")
        ]

    def test_accepts_atlassian_round_tripped_asterisk_bullets(self):
        body = (
            "* walletapi: add dependency.\n"
            "* infrastructure: provision KMS key.\n"
        )
        result = ps.parse_cross_app_bullets(body)
        assert [r[0] for r in result] == ["walletapi", "infrastructure"]


# ----- user story indices -----


class TestParseUserStoryIndices:
    def test_extracts_numbered_items(self):
        body = "1. As a user...\n2. As an admin...\n3. As payment-platform..."
        assert ps.parse_user_story_indices(body) == [1, 2, 3]

    def test_skips_non_numbered_paragraphs(self):
        body = "Intro paragraph.\n\n1. First.\n2. Second."
        assert ps.parse_user_story_indices(body) == [1, 2]

    def test_empty_returns_empty(self):
        assert ps.parse_user_story_indices("") == []


# ----- research/design detection -----


class TestResearchDetection:
    def test_spike_marker(self):
        text = "Spike: confirm SQS retry semantics."
        clusters = ps.detect_research_clusters(text)
        assert clusters == [("Research", "confirm SQS retry semantics.")]

    def test_research_marker(self):
        text = "Research: how does the ledger handle duplicates?"
        clusters = ps.detect_research_clusters(text)
        assert clusters[0][0] == "Research"

    def test_tbd_marker(self):
        text = "TBD: pricing model for cross-rail transfers."
        clusters = ps.detect_research_clusters(text)
        assert clusters and clusters[0][0] == "Research"

    def test_design_marker_classified_as_design(self):
        text = "Design: UX flow for the new transfer surface."
        clusters = ps.detect_research_clusters(text)
        assert clusters == [("Design", "UX flow for the new transfer surface.")]

    def test_inline_unclear_marker(self):
        text = "The retry policy is unclear and needs investigation soon."
        clusters = ps.detect_research_clusters(text)
        assert clusters and clusters[0][0] == "Research"

    def test_dedupes_repeated_markers(self):
        text = "Spike: same thing.\nSpike: same thing."
        clusters = ps.detect_research_clusters(text)
        assert len(clusters) == 1

    def test_caps_at_five(self):
        text = "\n".join(f"Spike: thing {i}" for i in range(10))
        clusters = ps.detect_research_clusters(text)
        assert len(clusters) == 5

    def test_no_markers_yields_empty(self):
        assert ps.detect_research_clusters("Plain prose, no markers.") == []


# ----- weak components -----


class TestWeaklyConnectedComponents:
    def test_single_component(self):
        nodes = ["A", "B", "C"]
        edges = [("A", "B"), ("B", "C")]
        assert ps.weakly_connected_components(nodes, edges) == [["A", "B", "C"]]

    def test_two_components(self):
        nodes = ["A", "B", "C", "D"]
        edges = [("A", "B"), ("C", "D")]
        components = ps.weakly_connected_components(nodes, edges)
        assert sorted(components) == [["A", "B"], ["C", "D"]]

    def test_isolated_nodes_each_own_component(self):
        nodes = ["A", "B", "C"]
        edges = []
        components = ps.weakly_connected_components(nodes, edges)
        assert sorted(components) == [["A"], ["B"], ["C"]]

    def test_ignores_edges_outside_node_set(self):
        nodes = ["A", "B"]
        edges = [("A", "C"), ("B", "D")]
        components = ps.weakly_connected_components(nodes, edges)
        assert sorted(components) == [["A"], ["B"]]


# ----- end-to-end synthesis -----


class TestSynthesize:
    def test_single_repo_yields_one_slice(self):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. As a user...",
                "Cross-Application Impact": "- payment-platform: extend the thing.",
                "Implementation Decisions": "Use Foo.",
                "Out of Scope": "- Z",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        graphify = {"modules": [{"name": "Foo", "repo": "payment-platform"}], "edges": []}
        slices = ps.synthesize(parent, graphify)
        assert len(slices) == 1
        assert slices[0].component == "payment-platform"
        assert slices[0].letter == "A"
        assert slices[0].type == "Task"
        assert slices[0].depends_on == []

    def test_multi_repo_yields_n_slices_in_order(self):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. story",
                "Cross-Application Impact": (
                    "- payment-platform: A.\n"
                    "- walletapi: B.\n"
                    "- infrastructure: C.\n"
                ),
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        graphify = {"modules": [], "edges": []}
        slices = ps.synthesize(parent, graphify)
        assert [s.component for s in slices] == [
            "payment-platform",
            "walletapi",
            "infrastructure",
        ]
        assert [s.letter for s in slices] == ["A", "B", "C"]

    def test_split_heuristic_disjoint_modules(self):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. story",
                "Cross-Application Impact": "- payment-platform: do many things.",
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        graphify = {
            "modules": [
                {"name": "Foo", "repo": "payment-platform"},
                {"name": "Bar", "repo": "payment-platform"},
                {"name": "Quux", "repo": "payment-platform"},
            ],
            "edges": [{"from": "Foo", "to": "Bar"}],
        }
        slices = ps.synthesize(parent, graphify)
        # Foo+Bar form one component; Quux is isolated → 2 slices.
        assert len(slices) == 2
        assert {s.letter for s in slices} == {"A", "B"}
        assert all(s.component == "payment-platform" for s in slices)

    def test_ordering_heuristic_cross_slice_edge(self):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. story",
                "Cross-Application Impact": (
                    "- payment-platform: A.\n- walletapi: B.\n"
                ),
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        graphify = {
            "modules": [
                {"name": "Saga", "repo": "payment-platform"},
                {"name": "Ledger", "repo": "walletapi"},
            ],
            "edges": [{"from": "Saga", "to": "Ledger"}],
        }
        slices = ps.synthesize(parent, graphify)
        a, b = slices  # A=payment-platform, B=walletapi
        # Saga is in A and depends on Ledger in B → A.depends_on includes B.
        assert a.depends_on == ["B"]
        assert b.depends_on == []

    def test_research_slice_added_for_spike_marker(self):
        sections = {
            "Problem Statement": "X",
            "Solution": "Y",
            "User Stories": "1. story",
            "Cross-Application Impact": "- payment-platform: A.",
            "Implementation Decisions": "Spike: confirm retry semantics.",
            "Out of Scope": "n/a",
            "Further Notes": "n/a",
            "References": "n/a",
        }
        parent = _normalized_parent(sections=sections)
        slices = ps.synthesize(parent, {"modules": [], "edges": []})
        types = [s.type for s in slices]
        assert "Research" in types
        # Research slice carries the primary component.
        research_slice = next(s for s in slices if s.type == "Research")
        assert research_slice.component == "payment-platform"
        assert research_slice.title.startswith("Spike:")

    def test_design_slice_added_for_design_marker(self):
        sections = {
            "Problem Statement": "X",
            "Solution": "Y",
            "User Stories": "1. story",
            "Cross-Application Impact": "- payment-platform: A.",
            "Implementation Decisions": "Design: UX flow.",
            "Out of Scope": "n/a",
            "Further Notes": "n/a",
            "References": "n/a",
        }
        parent = _normalized_parent(sections=sections)
        slices = ps.synthesize(parent, {"modules": [], "edges": []})
        types = [s.type for s in slices]
        assert "Design" in types

    def test_fallback_when_cross_app_empty(self):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. story",
                "Cross-Application Impact": "(none)",
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        slices = ps.synthesize(parent, {"modules": [], "edges": []})
        assert len(slices) == 1
        assert slices[0].component == "payment-platform"

    def test_too_many_slices_raises(self):
        bullets = "\n".join(
            f"- payment-platform: thing {i}." for i in range(30)
        )
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. story",
                "Cross-Application Impact": bullets,
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        # All 30 bullets land as separate slices; alphabet caps at 26.
        with pytest.raises(ValueError, match="too many proposed slices"):
            ps.synthesize(parent, {"modules": [], "edges": []})


# ----- rendering -----


class TestRender:
    def test_acceptance_criteria_stub_always_contains_TODO(self):
        text = ps._render_acceptance_criteria_stub([1, 2])
        assert "TODO" in text

    def test_render_includes_all_keyvalue_keys(self):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "P",
                "Solution": "S",
                "User Stories": "1. s",
                "Cross-Application Impact": "- payment-platform: extend.",
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        slices = ps.synthesize(parent, {"modules": [], "edges": []})
        rendered = ps.render_slice_plan(parent, slices, generated_on="2026-05-02T16:59Z")
        assert "## Slice A —" in rendered
        for key in (
            "- type:",
            "- title:",
            "- component:",
            "- depends_on:",
            "- modules:",
            "- description: |",
        ):
            assert key in rendered

    def test_idempotent_byte_stable_output(self):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "P",
                "Solution": "S",
                "User Stories": "1. s",
                "Cross-Application Impact": (
                    "- payment-platform: A.\n- walletapi: B.\n"
                ),
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        graphify = {
            "modules": [
                {"name": "Saga", "repo": "payment-platform"},
                {"name": "Ledger", "repo": "walletapi"},
            ],
            "edges": [{"from": "Saga", "to": "Ledger"}],
        }
        a = ps.render_slice_plan(
            parent, ps.synthesize(parent, graphify), generated_on="2026-05-02T16:59Z"
        )
        b = ps.render_slice_plan(
            parent, ps.synthesize(parent, graphify), generated_on="2026-05-02T16:59Z"
        )
        assert a == b
        # Spot-check that the file ends with a newline (POSIX-friendly).
        assert a.endswith("\n")


# ----- CLI -----


def _write(tmp_path: Path, name: str, payload) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


class TestCLI:
    def test_phase1_no_graphify_emits_query_plan(self, tmp_path: Path, run_main):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. s",
                "Cross-Application Impact": "- payment-platform: extend.",
                "Implementation Decisions": "Spike: TBD",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        parent_path = _write(tmp_path, "parent.json", parent)
        code, out, err = run_main(ps.main, "--parent-fixture", str(parent_path))
        assert code == 0
        assert err == ""
        assert out["status"] == "needs_graphify"
        tools = [a["tool"] for a in out["actions"]]
        assert "graphify.search" in tools

    def test_phase2_writes_redline_and_returns_summary(self, tmp_path: Path, run_main):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. s",
                "Cross-Application Impact": "- payment-platform: extend.",
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        parent_path = _write(tmp_path, "parent.json", parent)
        graphify_path = _write(tmp_path, "graphify.json", {"modules": [], "edges": []})
        out_path = tmp_path / "out.md"
        code, out, err = run_main(
            ps.main,
            "--parent-fixture",
            str(parent_path),
            "--graphify-fixture",
            str(graphify_path),
            "--out",
            str(out_path),
            "--generated-on",
            "2026-05-02T16:59Z",
        )
        assert code == 0
        assert err == ""
        assert out["status"] == "ok"
        assert out["slice_count"] == 1
        assert out_path.exists()
        body = out_path.read_text(encoding="utf-8")
        assert body.startswith("# Slice plan for PLTPM-99001:")

    def test_existing_redline_refuses_without_force(self, tmp_path: Path, run_main):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. s",
                "Cross-Application Impact": "- payment-platform: extend.",
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        parent_path = _write(tmp_path, "parent.json", parent)
        graphify_path = _write(tmp_path, "graphify.json", {"modules": [], "edges": []})
        out_path = tmp_path / "out.md"
        out_path.write_text("user edits", encoding="utf-8")

        code, _, err = run_main(
            ps.main,
            "--parent-fixture",
            str(parent_path),
            "--graphify-fixture",
            str(graphify_path),
            "--out",
            str(out_path),
            "--generated-on",
            "2026-05-02T16:59Z",
        )
        assert code == 3
        assert "already exists" in err
        assert out_path.read_text(encoding="utf-8") == "user edits"

    def test_force_overwrites_existing(self, tmp_path: Path, run_main):
        parent = _normalized_parent(
            sections={
                "Problem Statement": "X",
                "Solution": "Y",
                "User Stories": "1. s",
                "Cross-Application Impact": "- payment-platform: extend.",
                "Implementation Decisions": "n/a",
                "Out of Scope": "n/a",
                "Further Notes": "n/a",
                "References": "n/a",
            }
        )
        parent_path = _write(tmp_path, "parent.json", parent)
        graphify_path = _write(tmp_path, "graphify.json", {"modules": [], "edges": []})
        out_path = tmp_path / "out.md"
        out_path.write_text("user edits", encoding="utf-8")

        code, _, _ = run_main(
            ps.main,
            "--parent-fixture",
            str(parent_path),
            "--graphify-fixture",
            str(graphify_path),
            "--out",
            str(out_path),
            "--force",
            "--generated-on",
            "2026-05-02T16:59Z",
        )
        assert code == 0
        body = out_path.read_text(encoding="utf-8")
        assert "user edits" not in body

    def test_invalid_parent_fixture_is_rejected(self, tmp_path: Path, run_main):
        parent_path = _write(tmp_path, "parent.json", {"not": "a parent"})
        code, _, err = run_main(ps.main, "--parent-fixture", str(parent_path))
        assert code == 2
        assert "normalized parent" in err


# ----- goldens -----


@pytest.mark.parametrize(
    "name,parent_key",
    [
        ("merchant-health", "PLTPM-99001"),
        ("transfers-v3", "PLTPM-99002"),
    ],
)
class TestSlicePlanGoldens:
    """Run propose_slices end-to-end against checked-in fixtures.

    These goldens lock the propose_slices output for the two reference scenarios.
    To update, run propose_slices.py manually against the fixtures with
    --generated-on 2026-05-02T16:59Z and copy the file in.
    """

    def test_byte_identical_to_checked_in_plan(
        self, tmp_path: Path, fixtures_dir: Path, name: str, parent_key: str
    ):
        parent_path = fixtures_dir / f"parent-{name}.json"
        graphify_path = fixtures_dir / f"graphify-{name}.json"
        expected_path = fixtures_dir / f"slice-plan-{name}.md"

        out_path = tmp_path / "out.md"
        code = ps.main(
            [
                "--parent-fixture",
                str(parent_path),
                "--graphify-fixture",
                str(graphify_path),
                "--out",
                str(out_path),
                "--generated-on",
                "2026-05-02T16:59Z",
            ]
        )
        assert code == 0, f"propose_slices.py failed for {name}"
        actual = out_path.read_text(encoding="utf-8")
        expected = expected_path.read_text(encoding="utf-8")
        assert actual == expected, (
            f"slice plan for {name} drifted from {expected_path}; "
            "regenerate the fixture if the change is intentional."
        )
