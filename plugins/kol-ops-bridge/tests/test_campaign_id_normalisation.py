"""Regression: ``campaign_id`` must reject / normalise sentinel strings.

Background: on 2026-05-27 a polluted set of ``kol_facts`` /
``kol_goal_state`` rows for identity 658 (``@mysweetsavannah``) was
discovered with ``campaign_id='null'`` (the 4-char string). The console
had built a URL with ``encodeURIComponent(null) → "null"`` and forwarded
that to the bridge, which had no input validation and persisted it as
if it were a real campaign id. See ``playground/
cleanup_null_string_campaign_id.py`` for the matching cleanup.

Three layers must reject this kind of garbage:

1. The pure helper ``_norm_campaign_id`` — coerces sentinel strings and
   whitespace/empty input to ``None``.
2. Body Pydantic models — the ``_CampaignIdNormaliserMixin`` runs the
   helper in ``mode="before"`` for every model that declares a
   ``campaign_id`` field. Optional fields silently become ``None``;
   required fields (``ArchiveBody.campaign_id``) trip a 422.
3. Path / query handlers — sentinel strings cannot be silently coerced
   away on routes like ``/campaigns/{campaign_id}/...`` (the campaign
   scope is the whole point of those routes). The
   ``_reject_null_sentinel_campaign_id`` guard raises HTTP 400 with a
   ``code: "invalid_campaign_id"`` payload the console can route on.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG_NAME = "kol_ops_bridge_pkg"


def _ensure_package() -> types.ModuleType:
    """Mirror the conftest's package bootstrap. Needed when a test in
    this file runs in isolation (no ``cal_db`` fixture to lazily prime
    the package). Importing ``plugin_api`` triggers a relative
    ``from . import cal``, which requires the synthetic parent package
    to already exist in ``sys.modules``."""
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = types.ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]
    sys.modules[_PKG_NAME] = pkg
    for sub in ("schema", "goals", "policies", "cal", "discovery_router"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}", _PLUGIN_ROOT / f"{sub}.py",
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


def _load_plugin_api():
    _ensure_package()
    fq = f"{_PKG_NAME}.plugin_api"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(
        fq, _PLUGIN_ROOT / "plugin_api.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Layer 1: _norm_campaign_id pure helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "garbage",
    [
        "null", "NULL", " Null ",
        "undefined", "UNDEFINED",
        "nan", "NaN",
        "none", "None",
        "", "   ", "\t\n",
        None,
    ],
)
def test_norm_helper_returns_none_for_sentinels_and_blanks(garbage):
    plugin_api = _load_plugin_api()
    assert plugin_api._norm_campaign_id(garbage) is None


@pytest.mark.parametrize(
    "real",
    [
        "SEB8008-20260525",
        "TS8125-TEST-20260521",
        "abc",
        " SEB8008 ",  # gets stripped but preserved
    ],
)
def test_norm_helper_preserves_real_ids(real):
    plugin_api = _load_plugin_api()
    out = plugin_api._norm_campaign_id(real)
    assert out is not None
    assert out == real.strip()


# ---------------------------------------------------------------------------
# Layer 2: Pydantic mixin on body models
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body_cls_name",
    [
        "FactsWriteBody",
        "FactsWriteMultiBody",
        "EscalationOpenBody",
        "ApprovalDecisionBody",
        "EventWriteBody",
    ],
)
def test_optional_body_field_coerces_null_string_to_none(body_cls_name):
    pytest.importorskip("fastapi")
    plugin_api = _load_plugin_api()
    cls = getattr(plugin_api, body_cls_name)

    base_payload = {
        "FactsWriteBody": {"namespace": "identity", "facts": {}},
        "FactsWriteMultiBody": {"namespaces": {}},
        "EscalationOpenBody": {"reason": "test"},
        "ApprovalDecisionBody": {"identity_id": 1, "decided_by": "me"},
        "EventWriteBody": {"identity_id": 1, "event_type": "x", "actor": "y"},
    }[body_cls_name]
    obj = cls(**base_payload, campaign_id="null")
    assert obj.campaign_id is None

    obj = cls(**base_payload, campaign_id="undefined")
    assert obj.campaign_id is None

    obj = cls(**base_payload, campaign_id="SEB8008-20260525")
    assert obj.campaign_id == "SEB8008-20260525"


def test_archive_body_rejects_null_string_as_422():
    """``ArchiveBody.campaign_id`` is required. After the mixin coerces
    ``"null"`` to ``None``, the required-field constraint surfaces a
    Pydantic validation error — operator sees a clean 422, not a silent
    write under a phantom campaign id."""
    pytest.importorskip("fastapi")
    plugin_api = _load_plugin_api()
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        plugin_api.ArchiveBody(
            campaign_id="null", outcome="completed",
        )
    # Sanity: a real id still works.
    obj = plugin_api.ArchiveBody(
        campaign_id="SEB8008-20260525", outcome="completed",
    )
    assert obj.campaign_id == "SEB8008-20260525"


# ---------------------------------------------------------------------------
# Layer 3: path-segment guard (HTTP 400 for sentinel strings)
# ---------------------------------------------------------------------------


def test_path_guard_rejects_sentinel_strings():
    pytest.importorskip("fastapi")
    plugin_api = _load_plugin_api()
    from fastapi import HTTPException

    for sentinel in ("null", "NULL", "undefined", "NaN", "none"):
        with pytest.raises(HTTPException) as ei:
            plugin_api._reject_null_sentinel_campaign_id(sentinel)
        assert ei.value.status_code == 400
        detail = ei.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == "invalid_campaign_id"


def test_path_guard_passes_real_ids():
    plugin_api = _load_plugin_api()
    assert plugin_api._reject_null_sentinel_campaign_id(
        "SEB8008-20260525",
    ) == "SEB8008-20260525"
