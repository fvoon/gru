"""Shared pytest fixtures for prd-to-jira-issues skill scripts."""

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


def _write_fixture(tmp_path: Path, name: str, payload: dict | list) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture
def parent_inline_payload() -> dict:
    """A minimally-valid inline (non-elevated) parent issue payload."""
    description = (
        "## Problem Statement\n"
        "Merchants cannot see processor health for their own scope.\n\n"
        "## Solution\n"
        "Extend ProcessorHealthClient with merchantId.\n\n"
        "## User Stories\n"
        "1. As a merchant ops user, I want to see processor health filtered to my "
        "merchant so I can decide whether to retry.\n\n"
        "## Cross-Application Impact\n"
        "- payment-platform: extend ProcessorHealthClient.\n\n"
        "## Implementation Decisions\n"
        "Reuse processor-health-cache module.\n\n"
        "## Out of Scope\n"
        "- walletapi changes\n\n"
        "## Further Notes\n"
        "None.\n\n"
        "## References\n"
        "Internal slack thread.\n"
    )
    return {
        "key": "PLTPM-99001",
        "fields": {
            "summary": "Merchant-scoped processor health",
            "issuetype": {"name": "Story"},
            "components": [{"name": "payment-platform"}],
            "description": description,
        },
    }


@pytest.fixture
def parent_inline_fixture(tmp_path: Path, parent_inline_payload: dict) -> Path:
    return _write_fixture(tmp_path, "parent_inline.json", parent_inline_payload)


@pytest.fixture
def parent_elevated_payload() -> dict:
    """An elevated parent: condensed description + Confluence URL; full body lives elsewhere."""
    description = (
        "**This is a significant PRD; the full text lives in Confluence.**\n\n"
        "**Confluence**: https://moneylion.atlassian.net/wiki/spaces/ENG/pages/123456/Transfers-v3\n\n"
        "## Problem Statement\n"
        "Transfers v3 unifies the cross-rail experience.\n\n"
        "## Out of Scope\n"
        "- Customer-facing rebrand\n\n"
        "## Cross-Application Impact\n"
        "- payment-platform: orchestrator changes.\n"
        "- walletapi: ledger split.\n"
    )
    return {
        "key": "PLTPM-99002",
        "fields": {
            "summary": "Transfers v3 unification",
            "issuetype": {"name": "Technical Story"},
            "components": [{"name": "payment-platform"}],
            "description": description,
        },
    }


@pytest.fixture
def parent_elevated_fixture(tmp_path: Path, parent_elevated_payload: dict) -> Path:
    return _write_fixture(tmp_path, "parent_elevated.json", parent_elevated_payload)


@pytest.fixture
def confluence_full_body() -> str:
    return (
        "# Transfers v3 unification PRD\n\n"
        "## Problem Statement\n"
        "Customers experience inconsistent flows across rails.\n\n"
        "## Solution\n"
        "Unify ACH, RTP, and book transfers under a single saga.\n\n"
        "## User Stories\n"
        "1. As a customer, I want a single transfer surface.\n"
        "2. As payment-platform, I want one saga to maintain.\n\n"
        "## Cross-Application Impact\n"
        "- payment-platform: extend TransferSaga.\n"
        "- walletapi: split ACH and RTP ledger entries.\n\n"
        "## Implementation Decisions\n"
        "TransferSaga becomes the orchestrator. walletapi grows two ledger writers.\n\n"
        "## Out of Scope\n"
        "- Customer rebrand\n\n"
        "## Further Notes\n"
        "Spike: confirm SQS retry semantics with infra.\n\n"
        "## References\n"
        "PLTPM-21000 — original ACH design.\n"
    )


@pytest.fixture
def confluence_fixture(tmp_path: Path, confluence_full_body: str) -> Path:
    payload = {
        "id": "123456",
        "url": "https://moneylion.atlassian.net/wiki/spaces/ENG/pages/123456/Transfers-v3",
        "body": confluence_full_body,
    }
    return _write_fixture(tmp_path, "confluence.json", payload)
