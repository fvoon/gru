"""Graphify harness helper.

Thin wrapper over `graph.json` (the static output of the `graphify` CLI) that
satisfies the three semantic intents emitted by gru skills:

    graphify.search      -> graphify.search(text, ...)
    graphify.get_node    -> graphify.get_node(node_id, ...)
    graphify.get_edges   -> graphify.get_edges(from_id, ...)

Skills emit action plans that name those tools verbatim; the agent in chat
translates each to a `python <abs path>/_lib/graphify.py <subcommand> ...`
shell exec. The helper consumes `graph.json` directly with stdlib JSON; no
NetworkX dependency.

Resolution order for `graph.json`:

    1. The `--graph` CLI flag / `graph_path=` keyword argument
    2. The `$GRAPHIFY_GRAPH_JSON` environment variable
    3. `~/payments-graph/graphify-out/graph.json` (default)

When no `graph.json` is reachable, `aggregate()` returns a `needs_synthesis`
contract (rather than raising) so the calling skill can fall back to
PRD-only fixture synthesis (the manual workaround used during the first
F1 demo run on PLTPM-21272).

The helper is read-only with respect to the graph; it never writes back.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_GRAPH_PATH = Path.home() / "payments-graph" / "graphify-out" / "graph.json"
ENV_VAR = "GRAPHIFY_GRAPH_JSON"

# Tokens shorter than this are dropped before substring matching to avoid
# matching short stop-words like "of", "to", "in".
MIN_TOKEN_LEN = 3

# Tokenisation: split on anything that isn't a word character. Hyphens and
# underscores split too, so `JsonEncryptionConfig` source paths with
# `EncryptionConfig.java` tokenise as ["EncryptionConfig", "java"].
_TOKEN_RE = re.compile(r"\W+", re.UNICODE)


class GraphifyUnavailable(RuntimeError):
    """Raised by the loader when `graph.json` cannot be read.

    `aggregate()` catches this and returns the `needs_synthesis` contract so
    callers can fall back to PRD-only synthesis. The lower-level functions
    (`search`, `get_node`, `get_edges`) propagate it — callers that want the
    fallback should go through `aggregate()`.
    """


# ----- path resolution and loading -----


def _resolve_graph_path(graph_path: Path | str | None) -> Path:
    if graph_path is not None:
        return Path(graph_path)
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    return DEFAULT_GRAPH_PATH


def _load_graph(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError as exc:
        raise GraphifyUnavailable(
            f"graph.json not found at {path}; set ${ENV_VAR} or pass --graph / graph_path"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphifyUnavailable(
            f"graph.json at {path} could not be read or parsed: {exc}"
        ) from exc


# ----- helpers -----


def _derive_repo(source_file: str | None) -> str:
    """The first path segment of `source_file` is the canonical repo name.

    Returns an empty string when source_file is falsy, so callers can still
    handle the node without crashing on a missing field.
    """
    if not source_file:
        return ""
    head, _sep, _tail = source_file.partition("/")
    return head


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-word boundaries, drop tokens shorter than MIN_TOKEN_LEN.

    Deduplicates while preserving first-seen order so scoring stays stable.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in _TOKEN_RE.split(text.lower()):
        if len(raw) >= MIN_TOKEN_LEN and raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def _node_haystack(node: dict) -> str:
    """Concatenate the searchable fields of a node, lowercased once."""
    parts = (
        node.get("norm_label") or "",
        (node.get("label") or "").lower(),
    )
    return " ".join(parts)


def _score_node(node: dict, tokens: list[str]) -> int:
    haystack = _node_haystack(node)
    return sum(1 for t in tokens if t in haystack)


def _augment_node(node: dict) -> dict:
    """Return a copy of `node` with a derived `repo` field appended."""
    out = dict(node)
    out["repo"] = _derive_repo(node.get("source_file"))
    return out


# ----- public API -----


def search(
    text: str,
    *,
    limit: int = 20,
    scope_repos: list[str] | None = None,
    graph_path: Path | str | None = None,
) -> list[dict]:
    """Substring search over node `norm_label` / `label` for tokens in `text`.

    Tokens shorter than ``MIN_TOKEN_LEN`` (3) are dropped — this avoids
    matching stop-words like "of" / "to" against every node.

    Returns up to ``limit`` hits, each `{"id", "label", "repo", "score"}`,
    sorted by score descending then by graph order. When ``scope_repos`` is
    provided, only nodes whose derived repo is in that list are returned.
    """
    tokens = _tokenize(text)
    if not tokens:
        return []

    graph = _load_graph(_resolve_graph_path(graph_path))
    scope = set(scope_repos) if scope_repos else None

    scored: list[tuple[int, int, dict]] = []
    for index, node in enumerate(graph.get("nodes", [])):
        score = _score_node(node, tokens)
        if score == 0:
            continue
        repo = _derive_repo(node.get("source_file"))
        if scope is not None and repo not in scope:
            continue
        scored.append((-score, index, {
            "id": node.get("id"),
            "label": node.get("label"),
            "repo": repo,
            "score": score,
        }))

    scored.sort()
    return [hit for _neg_score, _idx, hit in scored[:limit]]


def get_node(node_id: str, *, graph_path: Path | str | None = None) -> dict | None:
    """Return the `nodes[]` entry for `node_id` augmented with a derived `repo`.

    Returns ``None`` when no node with that id exists in the graph.
    """
    graph = _load_graph(_resolve_graph_path(graph_path))
    for node in graph.get("nodes", []):
        if node.get("id") == node_id:
            return _augment_node(node)
    return None


def get_edges(from_id: str, *, graph_path: Path | str | None = None) -> list[dict]:
    """Return outbound edges from `from_id`, normalised to `{from, to, type}`.

    `type` carries the link's `relation` field from graph.json. Inbound edges
    (links whose `target == from_id`) are filtered out — the gate scripts that
    consume this output expect outbound-only.
    """
    graph = _load_graph(_resolve_graph_path(graph_path))
    out: list[dict] = []
    for link in graph.get("links", []):
        if link.get("source") != from_id:
            continue
        out.append({
            "from": link.get("source"),
            "to": link.get("target"),
            "type": link.get("relation") or "",
        })
    return out


def aggregate(
    text: str,
    *,
    limit: int = 20,
    scope_repos: list[str] | None = None,
    graph_path: Path | str | None = None,
) -> dict:
    """Run search + get_edges and return the `{modules, edges}` fixture format.

    The returned `edges` list is filtered to edges whose **both endpoints**
    are in the search hit set — propose_slices.py's WCC heuristic is only
    meaningful within a known module set, so cross-set edges would create
    misleading apparent connectivity.

    On missing or unreadable graph.json, returns the `needs_synthesis`
    contract instead of raising. Callers can detect this via ``status`` and
    fall back to PRD-only fixture synthesis.
    """
    try:
        hits = search(text, limit=limit, scope_repos=scope_repos, graph_path=graph_path)
    except GraphifyUnavailable as exc:
        return {
            "status": "needs_synthesis",
            "reason": str(exc),
            "hint": (
                "graph.json was not reachable; build a fixture from the parent "
                "PRD's named modules (Cross-Application Impact + Implementation "
                "Decisions sections) per the F1 PLTPM-21272 manual workaround."
            ),
        }

    module_ids = {h["id"] for h in hits}
    modules = [{"name": h["label"], "repo": h["repo"]} for h in hits]

    edges: list[dict] = []
    for hit_id in module_ids:
        for edge in get_edges(hit_id, graph_path=graph_path):
            if edge["to"] in module_ids:
                edges.append(edge)

    return {"modules": modules, "edges": edges}


# ----- CLI -----


def _emit(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def _cli_search(args: argparse.Namespace) -> int:
    try:
        hits = search(
            args.text,
            limit=args.limit,
            scope_repos=args.scope_repos,
            graph_path=args.graph,
        )
    except GraphifyUnavailable as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    _emit(hits)
    return 0


def _cli_get_node(args: argparse.Namespace) -> int:
    try:
        node = get_node(args.id, graph_path=args.graph)
    except GraphifyUnavailable as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    _emit(node)
    return 0


def _cli_get_edges(args: argparse.Namespace) -> int:
    try:
        edges = get_edges(args.from_id, graph_path=args.graph)
    except GraphifyUnavailable as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2
    _emit(edges)
    return 0


def _cli_aggregate(args: argparse.Namespace) -> int:
    # `aggregate()` swallows GraphifyUnavailable on its own and returns the
    # needs_synthesis contract — exit 0 in both cases so callers can branch
    # on the `status` field rather than the exit code.
    fixture = aggregate(
        args.text,
        limit=args.limit,
        scope_repos=args.scope_repos,
        graph_path=args.graph,
    )
    _emit(fixture)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Graphify harness helper for gru skills.",
        prog="graphify.py",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _add_graph(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--graph",
            type=Path,
            default=None,
            help=(
                f"Path to graph.json. Defaults to ${ENV_VAR} or "
                f"{DEFAULT_GRAPH_PATH} when neither is set."
            ),
        )

    p_search = sub.add_parser("search", help="Substring search over node labels.")
    p_search.add_argument("--text", required=True, help="Free-text search query.")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--scope-repos", nargs="+", default=None)
    _add_graph(p_search)
    p_search.set_defaults(func=_cli_search)

    p_node = sub.add_parser("get_node", help="Fetch one node by id.")
    p_node.add_argument("--id", required=True, dest="id")
    _add_graph(p_node)
    p_node.set_defaults(func=_cli_get_node)

    p_edges = sub.add_parser("get_edges", help="Fetch outbound edges from a node.")
    p_edges.add_argument("--from-id", required=True, dest="from_id")
    _add_graph(p_edges)
    p_edges.set_defaults(func=_cli_get_edges)

    p_agg = sub.add_parser(
        "aggregate",
        help="One-shot search + edges, returns {modules, edges} fixture.",
    )
    p_agg.add_argument("--text", required=True)
    p_agg.add_argument("--limit", type=int, default=20)
    p_agg.add_argument("--scope-repos", nargs="+", default=None)
    _add_graph(p_agg)
    p_agg.set_defaults(func=_cli_aggregate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
