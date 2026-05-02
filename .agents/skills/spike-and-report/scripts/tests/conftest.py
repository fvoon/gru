"""Shared pytest fixtures for spike-and-report skill scripts."""

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

    `parsed_stdout` is the JSON-decoded stdout if it parses, else None. Used by every
    CLI integration test across the three scripts.
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


# ---------- conventions fixtures ----------


_GOOD_CONVENTIONS = """\
# Jira conventions for gru

> Source of truth.
>
> **Schema version**: 2

## Site

- **Cloud ID**: `cloud-abc`

## Confluence

Required by skills.

- **Confluence space key**: `PLTPM`
- **Confluence space ID**: `1048576`
- **Spikes parent page ID** (where research sub-pages land): `9876543`
- **Spikes parent page title**: `Spikes`

## Issue types (PLTPM)

| Logical role | Type name | ID |
|---|---|---|
| Child (long research) | `Research ` | `10720` |
"""


_PLACEHOLDER_CONVENTIONS = """\
# Jira conventions

## Confluence

- **Confluence space key**: `<TBA — bootstrap>`
- **Confluence space ID**: `<TBA — bootstrap>`
- **Spikes parent page ID** (...): `<TBA — bootstrap>`
"""


_NO_SECTION_CONVENTIONS = """\
# Jira conventions

## Site

- **Cloud ID**: `cloud-abc`

## Issue types (PLTPM)

| Type | ID |
|---|---|
| Research  | 10720 |
"""


@pytest.fixture
def good_conventions_path(tmp_path: Path) -> Path:
    return _write_fixture(tmp_path, "jira-conventions.md", _GOOD_CONVENTIONS)


@pytest.fixture
def placeholder_conventions_path(tmp_path: Path) -> Path:
    return _write_fixture(tmp_path, "jira-conventions-placeholder.md", _PLACEHOLDER_CONVENTIONS)


@pytest.fixture
def missing_section_conventions_path(tmp_path: Path) -> Path:
    return _write_fixture(tmp_path, "jira-conventions-no-section.md", _NO_SECTION_CONVENTIONS)


# ---------- research-ticket fixtures ----------


def _research_payload(
    *,
    key: str = "PLTPM-21500",
    summary: str = "Spike: do we need a transactional outbox for wallet-to-wallet events?",
    description: str = (
        "We need to decide whether the new wallet-to-wallet event publisher "
        "requires a transactional outbox or whether the existing Axon event "
        "store guarantees are sufficient.\n\n"
        "Investigate: failure modes, retry semantics, dedup story."
    ),
    issue_type: str = "Research ",
    status: str = "To Do",
    parent_key: str | None = "PLTPM-21000",
    comments: list[str] | None = None,
) -> dict:
    """Build a Research-ticket Jira payload that mirrors atlassian.getJiraIssue shape."""
    issuelinks: list[dict] = []
    if parent_key is not None:
        issuelinks.append(
            {
                "type": {
                    "name": "Implement",
                    "outward": "implements",
                    "inward": "is implemented by",
                },
                "outwardIssue": {"key": parent_key, "fields": {"summary": "Parent PRD"}},
            }
        )
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": description,
            "issuetype": {"name": issue_type, "id": "10720"},
            "status": {"name": status},
            "components": [],
            "issuelinks": issuelinks,
            "comment": {"comments": [{"body": c} for c in (comments or [])]},
        },
    }


@pytest.fixture
def research_payload() -> dict:
    """Default Research ticket: To Do, Implement-linked parent, no comments."""
    return _research_payload()


@pytest.fixture
def research_orphan_payload() -> dict:
    return _research_payload(parent_key=None)


@pytest.fixture
def research_done_payload() -> dict:
    return _research_payload(status="Done")


@pytest.fixture
def research_with_comment_link_payload() -> dict:
    return _research_payload(
        comments=[
            "Spike report posted: "
            "https://moneylion.atlassian.net/wiki/spaces/PLTPM/pages/9999/Outbox-spike"
        ]
    )


@pytest.fixture
def research_wrong_type_payload() -> dict:
    return _research_payload(issue_type="Task")


@pytest.fixture
def research_fixture(tmp_path: Path, research_payload: dict) -> Path:
    return _write_fixture(tmp_path, "research.json", research_payload)


@pytest.fixture
def research_orphan_fixture(tmp_path: Path, research_orphan_payload: dict) -> Path:
    return _write_fixture(tmp_path, "research-orphan.json", research_orphan_payload)


# ---------- parent-PRD fixtures ----------


@pytest.fixture
def parent_inline_payload() -> dict:
    """Parent Story / Tech Story with an inline (non-elevated) PRD body."""
    return {
        "key": "PLTPM-21000",
        "fields": {
            "summary": "Wallet-to-wallet transfer events",
            "description": (
                "## Problem Statement\nDocs only.\n\n"
                "## Solution\nPublish wallet-to-wallet events.\n"
            ),
            "issuetype": {"name": "Technical Story"},
        },
    }


@pytest.fixture
def parent_elevated_payload() -> dict:
    """Parent with the elevated lead-in + a Confluence URL."""
    return {
        "key": "PLTPM-21000",
        "fields": {
            "summary": "Wallet-to-wallet transfer events",
            "description": (
                "**This is a significant PRD; the full text lives in Confluence.**\n\n"
                "**Confluence**: "
                "https://moneylion.atlassian.net/wiki/spaces/PLTPM/pages/4444/Wallet-events\n"
            ),
            "issuetype": {"name": "Technical Story"},
        },
    }


@pytest.fixture
def parent_inline_fixture(tmp_path: Path, parent_inline_payload: dict) -> Path:
    return _write_fixture(tmp_path, "parent-inline.json", parent_inline_payload)


@pytest.fixture
def parent_elevated_fixture(tmp_path: Path, parent_elevated_payload: dict) -> Path:
    return _write_fixture(tmp_path, "parent-elevated.json", parent_elevated_payload)


@pytest.fixture
def confluence_payload() -> dict:
    return {
        "id": "4444",
        "url": "https://moneylion.atlassian.net/wiki/spaces/PLTPM/pages/4444/Wallet-events",
        "body": "# Wallet-events PRD\n\nFull body.",
    }


@pytest.fixture
def confluence_fixture(tmp_path: Path, confluence_payload: dict) -> Path:
    return _write_fixture(tmp_path, "confluence.json", confluence_payload)


# ---------- graphify fixture ----------


@pytest.fixture
def graphify_payload() -> dict:
    return {
        "nodes": [
            {"name": "WalletEventPublisher", "repo": "payment-platform", "kind": "class"},
            {"name": "WalletEventConsumer", "repo": "walletapi", "kind": "class"},
        ],
        "edges": [
            {"from": "WalletEventPublisher", "to": "WalletEventConsumer", "type": "calls"},
        ],
        "communities": [
            {
                "name": "Wallet event flow",
                "members": ["WalletEventPublisher", "WalletEventConsumer"],
            },
        ],
    }


@pytest.fixture
def graphify_fixture(tmp_path: Path, graphify_payload: dict) -> Path:
    return _write_fixture(tmp_path, "graphify.json", graphify_payload)


# ---------- target JSON fixtures (validate_spike_target.py "ok" output) ----------


def _target_payload(
    *,
    research_key: str = "PLTPM-21500",
    parent_key: str | None = "PLTPM-21000",
    confluence_parent_path: str = "elevated-parent",
    confluence_parent_page_id: str = "4444",
    spikes_parent_page_id: str = "9876543",
    repos: list[str] | None = None,
    graphify: dict | None = None,
) -> dict:
    if repos is None:
        repos = ["payment-platform"]
    if graphify is None:
        graphify = {
            "nodes": [
                {"name": "WalletEventPublisher", "repo": "payment-platform", "kind": "class"},
            ],
            "edges": [],
            "communities": [],
        }
    return {
        "status": "ok",
        "research_key": research_key,
        "research_summary": (
            "Spike: do we need a transactional outbox for wallet-to-wallet events?"
        ),
        "research_question": (
            "Investigate whether the new event publisher needs a transactional outbox "
            "or whether the Axon event store guarantees suffice."
        ),
        "parent_key": parent_key,
        "confluence_parent_page_id": confluence_parent_page_id,
        "confluence_parent_path": confluence_parent_path,
        "confluence_space_key": "PLTPM",
        "confluence_space_id": "1048576",
        "spikes_parent_page_id": spikes_parent_page_id,
        "repos": repos,
        "graphify": graphify,
    }


@pytest.fixture
def target_payload() -> dict:
    return _target_payload()


@pytest.fixture
def target_multi_repo_payload() -> dict:
    return _target_payload(
        repos=["payment-platform", "walletapi"],
        graphify={
            "nodes": [
                {"name": "WalletEventPublisher", "repo": "payment-platform", "kind": "class"},
                {"name": "WalletEventConsumer", "repo": "walletapi", "kind": "class"},
            ],
            "edges": [
                {"from": "WalletEventPublisher", "to": "WalletEventConsumer", "type": "calls"},
            ],
            "communities": [
                {
                    "name": "Wallet event flow",
                    "members": ["WalletEventPublisher", "WalletEventConsumer"],
                },
            ],
        },
    )


@pytest.fixture
def target_orphan_payload() -> dict:
    return _target_payload(
        parent_key=None,
        confluence_parent_path="orphan",
        confluence_parent_page_id="9876543",  # falls back to Spikes parent
    )


@pytest.fixture
def target_fixture(tmp_path: Path, target_payload: dict) -> Path:
    return _write_fixture(tmp_path, "target.json", target_payload)


@pytest.fixture
def target_multi_repo_fixture(tmp_path: Path, target_multi_repo_payload: dict) -> Path:
    return _write_fixture(tmp_path, "target-multi.json", target_multi_repo_payload)


@pytest.fixture
def target_orphan_fixture(tmp_path: Path, target_orphan_payload: dict) -> Path:
    return _write_fixture(tmp_path, "target-orphan.json", target_orphan_payload)


# ---------- state file fixtures (for write_spike_report.py) ----------


def _state_payload(
    *,
    research_key: str = "PLTPM-21500",
    parent_key: str | None = "PLTPM-21000",
    started_at: str = "2026-05-02T18:00:00+00:00",
    confluence_parent_page_id: str = "4444",
    confluence_parent_path: str = "elevated-parent",
    spikes_parent_page_id: str = "9876543",
    repos: list[str] | None = None,
) -> dict:
    if repos is None:
        repos = ["payment-platform"]
    return {
        "schema_version": 1,
        "started_at": started_at,
        "research_key": research_key,
        "research_summary": "Spike: do we need a transactional outbox for wallet-to-wallet events?",
        "parent_key": parent_key,
        "confluence_parent_page_id": confluence_parent_page_id,
        "confluence_parent_path": confluence_parent_path,
        "spikes_parent_page_id": spikes_parent_page_id,
        "confluence_space_key": "PLTPM",
        "confluence_space_id": "1048576",
        "repos": repos,
        "worktree_paths": {
            r: f"/tmp/spike/{research_key}/{r}" for r in repos
        },
        "wall_clock_budget_minutes": 30,
        "turn_budget": 30,
    }


@pytest.fixture
def state_payload() -> dict:
    return _state_payload()


@pytest.fixture
def state_fixture(tmp_path: Path, state_payload: dict) -> Path:
    return _write_fixture(tmp_path, ".spike-state.json", state_payload)


@pytest.fixture
def state_orphan_fixture(tmp_path: Path) -> Path:
    return _write_fixture(
        tmp_path,
        "state-orphan.json",
        _state_payload(parent_key=None, confluence_parent_path="orphan",
                       confluence_parent_page_id="9876543"),
    )


# ---------- report fixtures ----------


_REDLINED_REPORT = """\
# Spike report — PLTPM-21500

> **Status:** DRAFT — fill in the `TODO` placeholders below.

## Question

**Spike: do we need a transactional outbox for wallet-to-wallet events?**

Investigate whether the new wallet-to-wallet event publisher requires a
transactional outbox or whether the existing Axon event store guarantees suffice.

## Existing state (from graphify)

- `WalletEventPublisher` lives in payment-platform.
- `WalletEventConsumer` lives in walletapi.

## What was tried

- Approach A: rely on Axon event store transactional guarantees only.
- Approach B: introduce a tiny outbox table with a poller publishing to SQS.

Code excerpt from `WalletEventPublisher.java`:

```java
public void publish(WalletEvent e) {
    eventStore.publish(e);
}
```

## What worked / what didn't

- Approach A is simpler but couples publisher availability to consumer health.
- Approach B adds a row write but isolates failure modes.

## Recommended approach

Approach B — a transactional outbox is worth the row-write cost because of
cross-region consumer flakiness we observed in the `infrastructure` SQS retry
profile.

### Recommended contract changes

- Add `wallet_outbox` table with columns `(id, payload, created_at, published_at)`.

## Suggested follow-up tickets

- `[BE] Add wallet_outbox table` — schema migration, no functional change.
- `[BE] Wire poller to SQS for wallet_outbox` — the outbox publisher.

## Spike metadata

_(auto-populated by write_spike_report.py at publish time.)_
"""


@pytest.fixture
def redlined_report_text() -> str:
    return _REDLINED_REPORT


@pytest.fixture
def redlined_report_fixture(tmp_path: Path, redlined_report_text: str) -> Path:
    return _write_fixture(tmp_path, "spike-PLTPM-21500-report.md", redlined_report_text)
