"""Shared pytest fixtures for ai-ready-check skill scripts."""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# Make the script directory importable when running pytest from any cwd.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def run_main(capsys) -> Callable:
    """Helper: invoke a script's `main(argv)` and return (exit_code, parsed_stdout, stderr).

    `parsed_stdout` is the JSON-decoded stdout if it parses, else None.
    """

    def _run(main_fn: Callable, *args: str) -> tuple[int, dict | None, str]:
        code = main_fn(list(args))
        captured = capsys.readouterr()
        parsed: dict | None = None
        if captured.out.strip():
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(captured.out)
        return code, parsed, captured.err

    return _run


def _write_fixture(tmp_path: Path, name: str, payload: dict | list | str) -> Path:
    p = tmp_path / name
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------- ticket / blocker payload builders ----------


def _build_implement_link(parent_key: str = "PLTPM-99001") -> dict:
    """Standard `Implement` outward link from a child to its parent."""
    return {
        "type": {
            "name": "Implement",
            "outward": "implements",
            "inward": "is implemented by",
        },
        "outwardIssue": {"key": parent_key, "fields": {"summary": "Parent PRD"}},
    }


def _build_blocker_link(blocker_key: str) -> dict:
    """Standard `Blocks` link with this ticket as the blocked side (blocker on
    inwardIssue, with `is blocked by` inward direction)."""
    return {
        "type": {
            "name": "Blocks",
            "outward": "blocks",
            "inward": "is blocked by",
        },
        "inwardIssue": {"key": blocker_key, "fields": {"summary": f"Blocker {blocker_key}"}},
    }


def build_ticket_payload(
    *,
    key: str = "PLTPM-21503",
    summary: str = "[BE] Wallet-to-wallet transfer event publisher",
    description: str | None = None,
    issue_type: str = "Technical Story",
    status: str = "next",
    components: list[str] | None = None,
    labels: list[str] | None = None,
    blocker_keys: list[str] | None = None,
    parent_key: str | None = "PLTPM-99001",
    extra_links: list[dict] | None = None,
    comments: list[str] | None = None,
) -> dict:
    """Build a Jira `getJiraIssue` payload for a child ticket.

    Defaults are the happy-path: status `next`, one canonical Component,
    `Technical Story`, and a non-empty `## Acceptance criteria` section. Pass
    overrides per test for failure modes.
    """
    if components is None:
        components = ["payment-platform"]
    if labels is None:
        labels = []
    if description is None:
        description = (
            "## What to build\n\n"
            "Publish wallet-to-wallet transfer events from payment-platform.\n\n"
            "## Acceptance criteria\n\n"
            "- [ ] Event is published on every successful transfer.\n"
            "- [ ] Unit tests cover the publisher.\n"
        )

    issuelinks: list[dict] = []
    if parent_key is not None:
        issuelinks.append(_build_implement_link(parent_key))
    for blocker_key in blocker_keys or []:
        issuelinks.append(_build_blocker_link(blocker_key))
    if extra_links:
        issuelinks.extend(extra_links)

    comment_block = {
        "comments": [
            {"id": str(i), "body": body} for i, body in enumerate(comments or [], start=1000)
        ],
    }

    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type, "id": "10001"},
            "status": {"name": status, "id": "14423"},
            "components": [{"name": c} for c in components],
            "labels": labels,
            "issuelinks": issuelinks,
            "comment": comment_block,
        },
    }


def build_blocker_payload(*, key: str, status: str = "Done") -> dict:
    """Build a tiny blocker payload (just key + status)."""
    return {
        "key": key,
        "fields": {"status": {"name": status, "id": "10001" if status == "Done" else "3"}},
    }


# ---------- pytest fixtures wrapping the builders ----------


@pytest.fixture
def make_ticket() -> Callable:
    return build_ticket_payload


@pytest.fixture
def make_blocker() -> Callable:
    return build_blocker_payload


@pytest.fixture
def ticket_passes_all() -> dict:
    return build_ticket_payload()


@pytest.fixture
def ticket_passes_all_fixture(tmp_path: Path, ticket_passes_all: dict) -> Path:
    return _write_fixture(tmp_path, "ticket-passes.json", ticket_passes_all)


@pytest.fixture
def ticket_fails_multi() -> dict:
    """Multiple checks fail at once: wrong status + wrong component + missing AC."""
    return build_ticket_payload(
        status="To Do",
        components=["wrong-component", "another-wrong"],
        description="No AC section here.",
    )


@pytest.fixture
def ticket_fails_multi_fixture(tmp_path: Path, ticket_fails_multi: dict) -> Path:
    return _write_fixture(tmp_path, "ticket-fails-multi.json", ticket_fails_multi)


@pytest.fixture
def blocker_done() -> dict:
    return build_blocker_payload(key="PLTPM-21504", status="Done")


@pytest.fixture
def blocker_in_progress() -> dict:
    return build_blocker_payload(key="PLTPM-21505", status="In Progress")
