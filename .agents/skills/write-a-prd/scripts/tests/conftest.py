"""Shared pytest fixtures for write-a-prd skill scripts."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

CANONICAL_COMPONENTS = [
    "payment-platform",
    "walletapi",
    "infrastructure",
    "spring-boot-starters",
]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def run_main(capsys) -> Callable:
    """Helper: invoke a script's main(argv) and return (exit_code, parsed_stdout, stderr).

    parsed_stdout is the JSON-decoded stdout if it parses, else None. Used by the CLI
    integration tests across all 3 scripts.
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


@pytest.fixture
def conventions_text() -> str:
    """A minimal-but-realistic conventions markdown that exercises the parser."""
    return """# Jira conventions for gru

## Bootstrap checklist (one-time, manual)

### Jira Components — 4 to create

Admin URL: <https://moneylion.atlassian.net/jira/software/c/projects/PLTPM/components>

The following 4 Components MUST exist in PLTPM:

- `payment-platform`
- `walletapi`
- `infrastructure`
- `spring-boot-starters`

**Components policy (gru-managed tickets)**: gru tickets carry exactly one Component.

### Workflow / link-type sanity check

These were discovered live.

## Issue types (PLTPM)

| Logical role | PLTPM type name | ID |
|---|---|---|
| Parent (user-facing) | `Story` | `10001` |
"""


@pytest.fixture
def conventions_file(tmp_path: Path, conventions_text: str) -> Path:
    """A temp conventions file the scripts can read."""
    p = tmp_path / "jira-conventions.md"
    p.write_text(conventions_text, encoding="utf-8")
    return p


@pytest.fixture
def components_fixture_all_present(tmp_path: Path) -> Path:
    """Mocked atlassian.getJiraProjectComponents response with all 4 canonical present + extras."""
    payload = [
        {"id": "10100", "name": "payment-platform", "description": "Repo: payment-platform"},
        {"id": "10101", "name": "walletapi", "description": "Repo: walletapi"},
        {"id": "10102", "name": "infrastructure", "description": "Repo: infrastructure"},
        {
            "id": "10103",
            "name": "spring-boot-starters",
            "description": "Repo: spring-boot-starters",
        },
        {"id": "10200", "name": "Payment Processor(s)", "description": "Domain (legacy)"},
        {"id": "10201", "name": "Wallet (Transfers)", "description": "Domain (legacy)"},
    ]
    p = tmp_path / "components_all.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture
def components_fixture_some_missing(tmp_path: Path) -> Path:
    """Two canonical present, two missing."""
    payload = [
        {"id": "10100", "name": "payment-platform"},
        {"id": "10103", "name": "spring-boot-starters"},
        {"id": "10200", "name": "Payment Processor(s)"},
    ]
    p = tmp_path / "components_partial.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture
def components_fixture_all_missing(tmp_path: Path) -> Path:
    """Only domain Components present (canonical absent)."""
    payload = [
        {"id": "10200", "name": "Payment Processor(s)"},
        {"id": "10201", "name": "Wallet (Transfers)"},
    ]
    p = tmp_path / "components_none.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p
