"""Identity + alias resolution tests."""

from __future__ import annotations


def test_upsert_identity_returns_id_and_is_idempotent(cal_db):
    a = cal_db.upsert_identity(handle="kathy", primary_email="k@x.com")
    b = cal_db.upsert_identity(handle="kathy", display_name="Kathy P")
    assert a is not None and a == b
    row = cal_db.get_identity(a)
    assert row["handle"] == "kathy"
    assert row["primary_email"] == "k@x.com"
    assert row["display_name"] == "Kathy P"


def test_env_isolation_creates_separate_identities(cal_db):
    live = cal_db.upsert_identity(handle="x", env="LIVE")
    test = cal_db.upsert_identity(handle="x", env="TEST")
    assert live != test


def test_alias_resolution_prefers_first_match(cal_db):
    kid = cal_db.upsert_identity(handle="kathy")
    cal_db.add_alias(kol_identity_id=kid, kind="email", value="k@x.com")
    cal_db.add_alias(kol_identity_id=kid, kind="gmail_thread_id", value="t-1")

    # First non-empty match wins.
    got = cal_db.resolve_identity(aliases=[
        ("gmail_thread_id", ""),
        ("email", "k@x.com"),
        ("handle", "kathy"),
    ])
    assert got == kid


def test_alias_collision_keeps_original_pointer(cal_db, caplog):
    a = cal_db.upsert_identity(handle="a")
    b = cal_db.upsert_identity(handle="b")
    cal_db.add_alias(kol_identity_id=a, kind="email", value="shared@x.com")

    # Re-adding the same alias under a different identity must NOT overwrite.
    with caplog.at_level("WARNING"):
        cal_db.add_alias(kol_identity_id=b, kind="email", value="shared@x.com")
    assert cal_db.resolve_identity(aliases=[("email", "shared@x.com")]) == a
    assert any("alias collision" in r.message for r in caplog.records)


def test_resolve_identity_returns_none_when_no_match(cal_db):
    assert cal_db.resolve_identity(aliases=[("email", "nobody@x.com")]) is None
