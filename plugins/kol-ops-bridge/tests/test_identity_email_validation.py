"""Regression: ``primary_email`` must look like ``x@y.tld``.

Before this guard landed (2026-05-27), the orchestrator's discovery
skill hand-rolled an ``upsert-identity`` call that put link-in-bio URLs
(``linktr.ee/TheCozyFarmhouse``), personal-site domains
(``thediyplaybook.com``), and even brand display names
(``My Sweet Savannah``) into ``kol_identity.primary_email`` — which
downstream outreach skills then tried to use as a ``to:`` header.

Two layers must reject garbage:

1. ``cal.upsert_identity`` — in-process safety net so direct importers
   (tests, other plugins) cannot bypass the HTTP layer.
2. ``IdentityUpsertBody`` Pydantic body — surfaces a clean 422 at the
   bridge HTTP boundary (which is what the CLI POSTs against).

Valid emails are normalized (stripped, lowercased) at both layers.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_api(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.plugin_api"
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
# CAL layer (cal.upsert_identity)
# ---------------------------------------------------------------------------


def test_cal_accepts_valid_email_and_lowercases(cal_db):
    iid = cal_db.upsert_identity(
        primary_handle="alice", primary_email="Alice@Example.COM", env="TEST",
    )
    row = cal_db.get_identity(iid)
    assert row["primary_email"] == "alice@example.com"


def test_cal_treats_none_as_unset(cal_db):
    iid = cal_db.upsert_identity(
        primary_handle="bob", primary_email=None, env="TEST",
    )
    row = cal_db.get_identity(iid)
    assert row["primary_email"] is None


def test_cal_treats_whitespace_as_unset(cal_db):
    iid = cal_db.upsert_identity(
        primary_handle="carol", primary_email="   ", env="TEST",
    )
    row = cal_db.get_identity(iid)
    assert row["primary_email"] is None


@pytest.mark.parametrize(
    "garbage",
    [
        "linktr.ee/TheCozyFarmhouse",
        "thediyplaybook.com",
        "My Sweet Savannah",
        "https://kolsite.com/contact",
        "email: foo@bar.com",
    ],
)
def test_cal_rejects_non_email_shape(cal_db, garbage):
    with pytest.raises(ValueError, match="primary_email must look like"):
        cal_db.upsert_identity(
            primary_handle="dave", primary_email=garbage, env="TEST",
        )


# ---------------------------------------------------------------------------
# Pydantic body (IdentityUpsertBody) — HTTP boundary
# ---------------------------------------------------------------------------


def test_pydantic_body_rejects_non_email_shape():
    pytest.importorskip("fastapi")
    plugin_api = _load_plugin_api()
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc:
        plugin_api.IdentityUpsertBody(
            primary_handle="x", primary_email="thediyplaybook.com",
        )
    msg = str(exc.value)
    assert "primary_email" in msg
    assert "x@y.tld" in msg


def test_pydantic_body_normalizes_valid_email():
    pytest.importorskip("fastapi")
    plugin_api = _load_plugin_api()
    body = plugin_api.IdentityUpsertBody(
        primary_handle="x", primary_email="  Foo@Example.COM  ",
    )
    assert body.primary_email == "foo@example.com"


def test_pydantic_body_none_passthrough():
    pytest.importorskip("fastapi")
    plugin_api = _load_plugin_api()
    body = plugin_api.IdentityUpsertBody(primary_handle="x", primary_email=None)
    assert body.primary_email is None
