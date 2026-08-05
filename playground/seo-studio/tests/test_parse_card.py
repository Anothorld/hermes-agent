"""Tests for POST /api/povison-products/parse-card endpoint.

Verifies the one-click URL → product card pipeline:
  1. Detail API lookup (name, image, specs, price)
  2. Review fetch (editorial only)
  3. LLM blurb generation

Each sub-step is independent (graceful degradation): a failed lookup,
review fetch, or LLM call returns null for that field rather than failing
the whole endpoint. Uses FastAPI TestClient with povison_catalog,
povison_reviews, and llm_client patched so no network/DB is hit.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import server  # noqa: E402


# llm_client lives in the skill's scripts dir, which the endpoint adds to
# sys.path at runtime. Tests can't rely on that, so register a stub module
# up front — patch("llm_client.chat", ...) then resolves against it.
if "llm_client" not in sys.modules:
    _stub = types.ModuleType("llm_client")
    _stub.chat = MagicMock(return_value=None)  # overridden per-test via patch
    sys.modules["llm_client"] = _stub


@pytest.fixture(scope="module")
def client():
    return TestClient(server.app)


def _detail_ok() -> dict:
    """A representative lookup_detail success payload."""
    return {
        "ok": True,
        "name": "Modern Media Console 90\"",
        "sku": "MC-90",
        "spu": "12345",
        "url": "https://www.povison.com/media-console-90",
        "image": "https://cdn.povison.com/img/mc-90.jpg",
        "media_types": ["image"],
        "specs": {
            "assembly_required": "No",
            "material": "Engineered Wood",
            "color": "Walnut",
            "number_of_drawers": 3,
            "style": "Modern",
        },
        "dimensions": {
            "overall": "90 × 18 × 28 in",
            "weight": "120 lbs",
        },
        "assembly": "No",
        "price": "$499.00",
        "review_count": 42,
    }


def _review() -> dict:
    return {
        "reviewId": 7,
        "nickname": "Jamie",
        "date": "2025-09-12",
        "title": "Sturdy and sleek",
        "detail": "Holds my 77\" OLED with room to spare. Assembly was a breeze.",
        "rating": 5,
        "helpfulCount": 4,
        "sourceType": "ORDER",
    }


# ---------------------------------------------------------------------------
# Specs mapping helpers
# ---------------------------------------------------------------------------
def test_flatten_dimensions_prefers_overall():
    d = server._flatten_dimensions({"overall": "90 × 18 × 28 in", "weight": "120 lbs"})
    assert d == "90 × 18 × 28 in"


def test_flatten_dimensions_joins_preferred_keys():
    d = server._flatten_dimensions({"width": "90", "depth": "18", "height": "28"})
    assert d == "90 × 18 × 28"


def test_flatten_dimensions_empty():
    assert server._flatten_dimensions({}) == ""
    assert server._flatten_dimensions(None) == ""


def test_infer_mechanism_no_assembly():
    d = {"assembly": "No", "specs": {"style": "Modern", "number_of_drawers": 3}}
    m = server._infer_mechanism(d)
    assert "no assembly required" in m
    assert "Modern" in m
    assert "3 drawers" in m


def test_infer_mechanism_yes_assembly():
    d = {"assembly": "Yes", "specs": {}}
    m = server._infer_mechanism(d)
    assert "assembly: Yes" in m


def test_map_detail_to_specs_editorial_shape():
    detail = _detail_ok()
    specs = server._map_detail_to_specs(detail)
    assert specs["dimensions"] == "90 × 18 × 28 in"
    assert specs["material"] == "Engineered Wood"
    assert specs["colors"] == "Walnut"  # singular API key → plural editorial key
    assert "mechanism" in specs
    assert "no assembly required" in specs["mechanism"]
    assert "Modern" in specs["mechanism"]
    assert "3 drawers" in specs["mechanism"]


def test_map_detail_to_specs_missing_fields():
    """Missing material/color should not produce empty specs entries."""
    detail = {"specs": {"material": "Wood"}, "dimensions": {"overall": "10 × 20 × 30"}}
    specs = server._map_detail_to_specs(detail)
    assert specs == {"dimensions": "10 × 20 × 30", "material": "Wood"}


# ---------------------------------------------------------------------------
# Endpoint — happy path (editorial)
# ---------------------------------------------------------------------------
def test_parse_card_editorial_happy_path(client):
    """Editorial mode: lookup + review + LLM all succeed → full card."""
    detail = _detail_ok()
    review = _review()
    fake_llm = MagicMock(return_value="A 90-word editorial blurb about the media console.")
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("povison_reviews.resolve_spu_by_url", return_value=12345), \
         patch("povison_reviews.fetch_reviews", return_value=[review]), \
         patch("llm_client.chat", new=fake_llm):
        r = client.post("/api/povison-products/parse-card", json={
            "url": "https://www.povison.com/media-console-90",
            "style": "editorial",
            "topic": {"primary_keyword": "media console"},
        })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "Modern Media Console 90\""
    assert body["image"] == "https://cdn.povison.com/img/mc-90.jpg"
    assert body["price"] == "$499.00"
    assert body["review_count"] == 42
    assert body["specs"]["dimensions"] == "90 × 18 × 28 in"
    assert body["specs"]["colors"] == "Walnut"
    assert body["reviewQuote"]["reviewer"] == "Jamie"
    assert body["reviewQuote"]["quote"].startswith("Holds my 77")
    assert body["reviewQuote"]["rating"] == 5
    assert body["blurb"].startswith("A 90-word editorial blurb")
    # LLM was called with system prompt mentioning editorial blurb length.
    sys_arg = fake_llm.call_args.args[0]
    assert "90-150 word" in sys_arg
    assert "specs paragraph" in sys_arg


# ---------------------------------------------------------------------------
# Endpoint — happy path (inline)
# ---------------------------------------------------------------------------
def test_parse_card_inline_happy_path(client):
    """Inline mode: lookup + LLM succeed; specs/reviewQuote are null."""
    detail = _detail_ok()
    fake_llm = MagicMock(return_value="A 40-word inline blurb.")
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("llm_client.chat", new=fake_llm):
        r = client.post("/api/povison-products/parse-card", json={
            "url": "https://www.povison.com/media-console-90",
            "style": "inline",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "Modern Media Console 90\""
    assert body["image"] == "https://cdn.povison.com/img/mc-90.jpg"
    # Inline mode skips specs and reviewQuote.
    assert body["specs"] is None
    assert body["reviewQuote"] is None
    assert body["blurb"] == "A 40-word inline blurb."
    sys_arg = fake_llm.call_args.args[0]
    assert "40-70 word" in sys_arg
    assert "90-150" not in sys_arg


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------
def test_parse_card_lookup_fails_returns_ok_false(client):
    """Detail API returns ok=False → endpoint returns ok=False, no 500."""
    with patch("povison_catalog.lookup_detail",
               return_value={"ok": False, "error": "product_not_found"}):
        r = client.post("/api/povison-products/parse-card", json={"url": "https://x"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "product_not_found" in body["error"]


def test_parse_card_lookup_raises_returns_ok_false(client):
    """Detail API raises → endpoint returns ok=False with the error message."""
    with patch("povison_catalog.lookup_detail", side_effect=RuntimeError("boom")):
        r = client.post("/api/povison-products/parse-card", json={"url": "https://x"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "boom" in body["error"]


def test_parse_card_review_fails_keeps_other_fields(client):
    """Review fetch raises → reviewQuote is null but name/image/blurb survive."""
    detail = _detail_ok()
    fake_llm = MagicMock(return_value="blurb text")
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("povison_reviews.resolve_spu_by_url", side_effect=RuntimeError("db down")), \
         patch("llm_client.chat", new=fake_llm):
        r = client.post("/api/povison-products/parse-card", json={
            "url": "https://x", "style": "editorial",
        })
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "Modern Media Console 90\""
    assert body["reviewQuote"] is None
    assert body["blurb"] == "blurb text"


def test_parse_card_review_returns_empty_keeps_other_fields(client):
    """resolve_spu_by_url returns None → reviewQuote null, rest intact."""
    detail = _detail_ok()
    fake_llm = MagicMock(return_value="blurb")
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("povison_reviews.resolve_spu_by_url", return_value=None), \
         patch("llm_client.chat", new=fake_llm):
        r = client.post("/api/povison-products/parse-card", json={
            "url": "https://x", "style": "editorial",
        })
    body = r.json()
    assert body["ok"] is True
    assert body["reviewQuote"] is None
    assert body["blurb"] == "blurb"


def test_parse_card_llm_fails_keeps_lookup_and_review(client):
    """LLM call raises → blurb null but name/image/specs/reviewQuote survive."""
    detail = _detail_ok()
    review = _review()
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("povison_reviews.resolve_spu_by_url", return_value=12345), \
         patch("povison_reviews.fetch_reviews", return_value=[review]), \
         patch("llm_client.chat", side_effect=RuntimeError("llm timeout")):
        r = client.post("/api/povison-products/parse-card", json={
            "url": "https://x", "style": "editorial",
        })
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "Modern Media Console 90\""
    assert body["reviewQuote"]["reviewer"] == "Jamie"
    assert body["blurb"] is None


def test_parse_card_llm_returns_empty_string_keeps_null(client):
    """LLM returns empty/whitespace string → blurb stays null."""
    detail = _detail_ok()
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("llm_client.chat", return_value="   "):
        r = client.post("/api/povison-products/parse-card", json={"url": "https://x"})
    body = r.json()
    assert body["ok"] is True
    assert body["blurb"] is None


def test_sanitize_rejects_cot_leak():
    raw = (
        "The user wants me to write a 90-150 word editorial blurb. "
        "Let me analyze the product details and count words. "
        "Paragraph 1: scene. Paragraph 2: specs."
    )
    blurb, reason = server._sanitize_parse_card_blurb(raw, style="editorial")
    assert blurb is None and reason == "cot_leak"


def test_sanitize_rejects_too_long():
    raw = " ".join(["word"] * 200)
    blurb, reason = server._sanitize_parse_card_blurb(raw, style="editorial")
    assert blurb is None and reason == "too_long"


def test_sanitize_accepts_normal_editorial_blurb():
    raw = (
        "OLED panels need a rigid platform, and this walnut media console "
        "delivers a mid-century silhouette with adjustable LED backlighting. "
        "Measuring 70.87 x 17.72 x 20.47 inches, it arrives fully assembled "
        "in engineered wood with a warm walnut color, giving slim OLED sets "
        "a stable, low-profile home in the living room without flat-pack wobble."
    )
    blurb, reason = server._sanitize_parse_card_blurb(raw, style="editorial")
    assert reason == "ok"
    assert blurb == raw.strip()


def test_parse_card_rejects_cot_blurb_keeps_other_fields(client):
    """CoT dump from LLM must not become product.blurb (stays null)."""
    detail = _detail_ok()
    cot = (
        "The user wants me to write a 90-150 word editorial blurb for a "
        "POVISON product. Let me analyze the product details and count words. "
        + (" filler" * 80)
    )
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("llm_client.chat", return_value=cot):
        r = client.post("/api/povison-products/parse-card", json={
            "url": "https://x", "style": "editorial",
        })
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "Modern Media Console 90\""
    assert body["blurb"] is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_parse_card_requires_url(client):
    """Missing url → 400 (not 200 with ok=False)."""
    r = client.post("/api/povison-products/parse-card", json={})
    assert r.status_code == 400


def test_parse_card_default_style_is_inline(client):
    """Omitting style defaults to inline (specs/reviewQuote null)."""
    detail = _detail_ok()
    with patch("povison_catalog.lookup_detail", return_value=detail), \
         patch("llm_client.chat", return_value="blurb"):
        r = client.post("/api/povison-products/parse-card", json={"url": "https://x"})
    body = r.json()
    assert body["ok"] is True
    assert body["specs"] is None
    assert body["reviewQuote"] is None
