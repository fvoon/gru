"""Shared pytest fixtures for the gru `_lib` shared helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `_lib/` importable when pytest is invoked from any cwd, so tests can do
# `import graphify` and `import markdown` against the canonical sources.
LIB_DIR = Path(__file__).resolve().parent.parent
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def tiny_graph_path() -> Path:
    """Path to the ~10-node fixture graph used across graphify tests."""
    return FIXTURES_DIR / "graph-tiny.json"
