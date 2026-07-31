"""Shared pytest fixtures for SEO Studio tests.

These tests exercise the playground's server.py / povison_reviews.py as
plain Python modules — they do NOT start the FastAPI server. Importing
``server`` is safe: it creates the ``app`` object at import time but does
not bind a socket.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SEO_STUDIO_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Make the seo-studio package importable so `import server` / `import
# povison_reviews` work from the test runner without touching sys.path at
# runtime in every test file.
if str(SEO_STUDIO_DIR) not in sys.path:
    sys.path.insert(0, str(SEO_STUDIO_DIR))

# Minimal env so auth imports / .env loader don't blow up under pytest.
os.environ.setdefault("SEO_SKILL_DIR", str(SEO_STUDIO_DIR / "scripts"))
os.environ.setdefault("SEO_RUNS_DIR", str(SEO_STUDIO_DIR / "runs"))
os.environ.setdefault("SEO_STUDIO_HTML", str(SEO_STUDIO_DIR / "ui" / "index.html"))


def load_fixture(name: str) -> dict:
    """Load a JSON fixture from tests/fixtures/ as a dict."""
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def golden_inline() -> dict:
    """Baseline inline-style article state (merged-flow inline links + accepted
    products + 1 rejected product + accepted/rejected internal links)."""
    return load_fixture("golden_inline.json")


@pytest.fixture
def golden_editorial() -> dict:
    """Baseline editorial-style article state (3 accepted product cards with
    reviewQuote/specs/warranty, no inline povison links in body sections)."""
    return load_fixture("golden_editorial.json")


@pytest.fixture
def editorial_2products() -> dict:
    """Editorial article state with only 2 accepted products — used to assert
    degradation (editorial render returns empty, falls back to inline)."""
    return load_fixture("editorial_2products.json")
