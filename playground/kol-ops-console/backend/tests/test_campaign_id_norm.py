"""Regression: console-backend layer rejects sentinel ``campaign_id``.

Pairs with ``plugins/kol-ops-bridge/tests/test_campaign_id_normalisation.py``
on the bridge side. The console catches sentinel strings up front so
the operator gets a tight, frontend-routable 400 instead of waiting on
a downstream "campaign not found" 404.

Three surfaces are tested:

1. The pure helper ``app.campaign_id_norm.norm_campaign_id`` — the
   single source of truth for what counts as a sentinel.
2. ``CampaignIdNormaliserMixin`` — applied to every body model that
   carries a ``campaign_id`` field on a write endpoint.
3. ``app.campaign_config_sync.assert_campaign_config_complete`` — the
   chokepoint hit by the three fresh-session draft paths
   (redraft / refine / preview-draft). Even with the bridge available
   it raises HTTP 400 ``invalid_campaign_id`` upfront, so a buggy
   frontend can't tie up gateway slots.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


# ---------------------------------------------------------------------------
# Layer 1: norm_campaign_id
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
def test_norm_returns_none_for_sentinels_and_blanks(garbage):
    from app.campaign_id_norm import norm_campaign_id

    assert norm_campaign_id(garbage) is None


@pytest.mark.parametrize(
    "real",
    [
        "SEB8008-20260525",
        "TS8125-TEST-20260521",
        "abc",
        "  SEB8008  ",
    ],
)
def test_norm_preserves_real_ids(real):
    from app.campaign_id_norm import norm_campaign_id

    out = norm_campaign_id(real)
    assert out == real.strip()


# ---------------------------------------------------------------------------
# Layer 2: mixin on body models
# ---------------------------------------------------------------------------


def test_set_email_body_normalises_null_string():
    """Operator filling email on a KOL detail page that arrived via a
    bad ``?campaign_id=null`` URL must not have ``"null"`` written to
    CAL as the campaign provenance. The mixin coerces it to None;
    the resulting facts row gets ``campaign_id IS NULL`` (a real
    identity-scoped fact, which is correct for a manual email fill)."""
    from app.routers.kols import SetEmailBody

    obj = SetEmailBody(
        email="a@b.com", campaign_id="null", env="LIVE",
    )
    assert obj.campaign_id is None

    obj = SetEmailBody(
        email="a@b.com", campaign_id="SEB8008-20260525", env="LIVE",
    )
    assert obj.campaign_id == "SEB8008-20260525"


def test_refine_body_rejects_null_string_as_422():
    """``RefineBody.campaign_id`` is required (``min_length=1``). The
    mixin coerces ``"null"`` to None first; Pydantic then surfaces a
    422 against the required-field rule — operator sees a clean error
    rather than the misleading "campaign_config missing" 400 we used
    to emit further downstream."""
    from pydantic import ValidationError

    from app.routers.approvals import RefineBody

    with pytest.raises(ValidationError):
        RefineBody(
            identity_id=1,
            campaign_id="null",
            refinement_prompt="please rewrite",
        )


# ---------------------------------------------------------------------------
# Layer 3: assert_campaign_config_complete fast-path 400
# ---------------------------------------------------------------------------


class _StubBridge:
    """Records whether ``get_campaign`` was even reached. The whole
    point of the fast-path 400 is that the bridge is NOT called for
    sentinel strings (avoids wasted RPC + the misleading 404 error
    body the operator used to see)."""

    def __init__(self) -> None:
        self.get_campaign_calls: list[str] = []

    async def get_campaign(self, campaign_id: str):
        self.get_campaign_calls.append(campaign_id)
        return {
            "campaign_id": campaign_id,
            "product_display_name": "the test sofa",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("sentinel", ["null", "undefined", "  ", "NaN", ""])
async def test_assert_complete_fast_fails_400_on_sentinel(sentinel):
    from fastapi import HTTPException

    from app.campaign_config_sync import assert_campaign_config_complete

    bridge = _StubBridge()
    with pytest.raises(HTTPException) as ei:
        await assert_campaign_config_complete(bridge, sentinel)  # type: ignore[arg-type]
    assert ei.value.status_code == 400
    detail = ei.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_campaign_id"
    # Bridge must not have been called — fast-fail or this defense is
    # purely cosmetic.
    assert bridge.get_campaign_calls == []


@pytest.mark.asyncio
async def test_assert_complete_proceeds_to_bridge_for_real_id():
    from app.campaign_config_sync import assert_campaign_config_complete

    bridge = _StubBridge()
    out = await assert_campaign_config_complete(bridge, "SEB8008-20260525")  # type: ignore[arg-type]
    assert out["campaign_id"] == "SEB8008-20260525"
    assert bridge.get_campaign_calls == ["SEB8008-20260525"]
