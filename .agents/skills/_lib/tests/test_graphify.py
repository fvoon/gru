"""Tests for the graphify harness helper.

Covers the public Python API (`search`, `get_node`, `get_edges`, `aggregate`),
the CLI entry point, and the `graph.json` resolution order. RED-first: this
file imports `graphify` which does not exist yet — every test should fail with
ModuleNotFoundError until the implementation lands.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import graphify

GRAPHIFY_PATH = Path(graphify.__file__)


# ----- search() -----


class TestSearch:
    def test_substring_match_against_norm_label(self, tiny_graph_path):
        hits = graphify.search("FundDetails", graph_path=tiny_graph_path)
        ids = [h["id"] for h in hits]
        assert "walletapi_funddetails" in ids

    def test_case_insensitive(self, tiny_graph_path):
        lower = graphify.search("funddetails", graph_path=tiny_graph_path)
        upper = graphify.search("FUNDDETAILS", graph_path=tiny_graph_path)
        mixed = graphify.search("FundDetails", graph_path=tiny_graph_path)
        assert sorted(h["id"] for h in lower) == sorted(h["id"] for h in upper)
        assert sorted(h["id"] for h in lower) == sorted(h["id"] for h in mixed)

    def test_scope_repos_filters_to_named_repo(self, tiny_graph_path):
        all_hits = graphify.search("Encrypt", graph_path=tiny_graph_path)
        sbs_only = graphify.search(
            "Encrypt", graph_path=tiny_graph_path, scope_repos=["spring-boot-starters"]
        )
        # Sanity: un-scoped search picks up walletapi's JsonEncryptionConfig too.
        all_repos = {h["repo"] for h in all_hits}
        assert "walletapi" in all_repos
        assert "spring-boot-starters" in all_repos
        # Scoped search drops walletapi entirely.
        assert all(h["repo"] == "spring-boot-starters" for h in sbs_only)
        assert len(sbs_only) >= 1

    def test_limit_caps_result_count(self, tiny_graph_path):
        hits = graphify.search("Fund", limit=2, graph_path=tiny_graph_path)
        assert len(hits) == 2

    def test_returns_id_label_repo_at_minimum(self, tiny_graph_path):
        hits = graphify.search("FundDetails", graph_path=tiny_graph_path)
        assert hits, "expected at least one hit for FundDetails"
        for h in hits:
            assert {"id", "label", "repo"} <= set(h.keys())

    def test_short_tokens_below_three_chars_are_dropped(self, tiny_graph_path):
        # "of" is too short to be a useful token; it should not match every node
        # whose source path happens to contain "of" inside another word.
        hits = graphify.search("of", graph_path=tiny_graph_path)
        assert hits == []


# ----- get_node() -----


class TestGetNode:
    def test_returns_node_with_derived_repo(self, tiny_graph_path):
        n = graphify.get_node("walletapi_funddetails", graph_path=tiny_graph_path)
        assert n is not None
        assert n["repo"] == "walletapi"
        assert n["label"] == "FundDetails"

    def test_repo_derived_for_each_canonical_path(self, tiny_graph_path):
        n_sbs = graphify.get_node(
            "spring_boot_starters_encryptedjsonattributeconverter",
            graph_path=tiny_graph_path,
        )
        n_infra = graphify.get_node("infrastructure_kmskey", graph_path=tiny_graph_path)
        assert n_sbs is not None and n_sbs["repo"] == "spring-boot-starters"
        assert n_infra is not None and n_infra["repo"] == "infrastructure"

    def test_missing_id_returns_none(self, tiny_graph_path):
        assert graphify.get_node("does_not_exist_xyz", graph_path=tiny_graph_path) is None


# ----- get_edges() -----


class TestGetEdges:
    def test_outbound_only(self, tiny_graph_path):
        # FundDetails has 1 outbound edge (-> JsonEncryptionConfig) and 3 inbound
        # (from FundOption, FundOptionRepository, JsonEncryptionConfig back-ref).
        # We must only return the outbound one.
        edges = graphify.get_edges("walletapi_funddetails", graph_path=tiny_graph_path)
        assert all(e["from"] == "walletapi_funddetails" for e in edges)
        targets = {e["to"] for e in edges}
        assert "walletapi_jsonencryptionconfig" in targets
        # None of the inbound source IDs should appear as `from` in the result.
        inbound_sources = {
            "walletapi_fundoption",
            "walletapi_fundoptionrepository",
            "walletapi_jsonencryptionconfig",
        }
        assert all(e["from"] not in inbound_sources for e in edges)

    def test_normalised_shape_uses_relation_as_type(self, tiny_graph_path):
        edges = graphify.get_edges("walletapi_funddetails", graph_path=tiny_graph_path)
        assert edges, "expected at least one outbound edge"
        for e in edges:
            assert {"from", "to", "type"} <= set(e.keys())
            # `type` is the link's `relation` field from graph.json.
            assert e["type"] == "configured-by"

    def test_node_with_no_outbound_returns_empty_list(self, tiny_graph_path):
        # HibernateEncryptionConfig is the target of "extends" but never a source.
        edges = graphify.get_edges(
            "spring_boot_starters_hibernateencryptionconfig", graph_path=tiny_graph_path
        )
        assert edges == []


# ----- aggregate() -----


class TestAggregate:
    def test_produces_modules_edges_shape(self, tiny_graph_path):
        fixture = graphify.aggregate("Fund", graph_path=tiny_graph_path)
        assert isinstance(fixture, dict)
        assert "modules" in fixture and "edges" in fixture
        for m in fixture["modules"]:
            assert {"name", "repo"} <= set(m.keys())
        for e in fixture["edges"]:
            assert {"from", "to", "type"} <= set(e.keys())

    def test_only_includes_edges_between_aggregated_modules(self, tiny_graph_path):
        # search("Fund") matches the four walletapi `Fund*` nodes. Their internal
        # outbound edges (FundOption->FundDetails, FundOptionRepo->FundOption,
        # FundOptionRepo->FundDetails, FundACHService->FundOptionRepo) must appear.
        # Edges that leave the hit set (FundDetails->JsonEncryptionConfig,
        # FundACHService->JsonEncryptionConfig) must NOT appear.
        fixture = graphify.aggregate("Fund", graph_path=tiny_graph_path)
        module_ids = {self._id_for_walletapi(m["name"]) for m in fixture["modules"]}
        for e in fixture["edges"]:
            assert (
                e["from"] in module_ids and e["to"] in module_ids
            ), f"edge {e} crosses outside the module set"
        # Sanity: at least one of the expected internal edges should have been kept.
        kept_pairs = {(e["from"], e["to"]) for e in fixture["edges"]}
        assert ("walletapi_fundoption", "walletapi_funddetails") in kept_pairs

    @staticmethod
    def _id_for_walletapi(name: str) -> str:
        return "walletapi_" + name.lower()

    def test_missing_graph_json_emits_needs_synthesis(self, tmp_path):
        nonexistent = tmp_path / "nope.json"
        result = graphify.aggregate("anything", graph_path=nonexistent)
        assert result["status"] == "needs_synthesis"
        assert "reason" in result and "hint" in result


# ----- CLI -----


class TestCLI:
    def test_search_subcommand_emits_valid_json(self, tiny_graph_path):
        out = subprocess.run(
            [
                sys.executable,
                str(GRAPHIFY_PATH),
                "search",
                "--text",
                "FundDetails",
                "--graph",
                str(tiny_graph_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        parsed = json.loads(out.stdout)
        assert isinstance(parsed, list)
        assert any(h["id"] == "walletapi_funddetails" for h in parsed)

    def test_aggregate_subcommand_emits_modules_edges(self, tiny_graph_path):
        out = subprocess.run(
            [
                sys.executable,
                str(GRAPHIFY_PATH),
                "aggregate",
                "--text",
                "Fund",
                "--graph",
                str(tiny_graph_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        parsed = json.loads(out.stdout)
        assert "modules" in parsed and "edges" in parsed


# ----- resolution order -----


class TestResolutionOrder:
    def test_env_var_used_when_no_graph_path_arg(self, tiny_graph_path, monkeypatch):
        monkeypatch.setenv("GRAPHIFY_GRAPH_JSON", str(tiny_graph_path))
        n = graphify.get_node("walletapi_funddetails")
        assert n is not None
        assert n["repo"] == "walletapi"

    def test_explicit_graph_path_wins_over_env_var(
        self, tiny_graph_path, tmp_path, monkeypatch
    ):
        # Point env var at a non-existent file; explicit arg should still win.
        monkeypatch.setenv("GRAPHIFY_GRAPH_JSON", str(tmp_path / "ignored.json"))
        n = graphify.get_node("walletapi_funddetails", graph_path=tiny_graph_path)
        assert n is not None
        assert n["repo"] == "walletapi"
