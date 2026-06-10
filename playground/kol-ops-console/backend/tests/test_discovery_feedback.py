"""Unit tests for shortlist decision feedback validation + capture."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import HTTPException

from app.discovery_feedback import (
    DecisionFeedbackBody,
    get_product_info,
    list_failed_shortlist_captures,
    record_decisions_safe,
    replay_shortlist_capture,
    resolve_per_kol_decisions,
    validate_decision_feedback,
)
from app.learned_criteria import learned_criteria_brief_section


class _BridgeStub:
    def __init__(
        self,
        *,
        comment_required: bool = True,
        fail: bool = False,
        fail_times: int = 0,
        tags: list[str] | None = None,
    ):
        self.comment_required = comment_required
        self.fail = fail
        self.fail_times = fail_times
        self.tags = tags if tags is not None else ["tone_match", "audience_fit", "other"]
        self.recorded: list[dict] = []
        self.record_calls = 0

    async def discovery_feedback_requirements(self, *, sku, env):
        if self.fail:
            from app.bridge_client import BridgeError

            raise BridgeError(502, "down")
        return {"comment_required": self.comment_required}

    async def list_discovery_tags(self, *, action=None, status="active"):
        if self.fail:
            from app.bridge_client import BridgeError

            raise BridgeError(502, "down")
        return {"tags": [{"tag": t} for t in self.tags]}

    async def record_shortlist_decision(self, body):
        self.record_calls += 1
        if self.fail or self.record_calls <= self.fail_times:
            raise RuntimeError("bridge down")
        self.recorded.append(body)
        return {"recorded": len(body["decisions"]), "event_ids": [1]}

    async def get_discovery_criteria(self, *, sku, env, max_chars):
        if self.fail:
            raise RuntimeError("bridge down")
        return {
            "sku": sku,
            "category": "chair",
            "spu_md": "SPU rules",
            "category_md": "Category rules",
        }


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (sku TEXT PRIMARY KEY, name TEXT, pitch_md TEXT);
        CREATE TABLE product_campaigns (sku TEXT, campaign_id TEXT, env TEXT);
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER, action TEXT, target TEXT,
            payload_json TEXT, ts TEXT
        );
        INSERT INTO products VALUES ('SKU1', 'Chair', 'A very ergonomic chair');
        INSERT INTO product_campaigns VALUES ('SKU1', 'camp-1', 'LIVE');
        """
    )
    return conn


def _feedback(**kw) -> DecisionFeedbackBody:
    return DecisionFeedbackBody(**kw)


class TestProductInfo:
    def test_resolves_sku_and_pitch(self):
        info = get_product_info(_conn(), campaign_id="camp-1", env="LIVE")
        assert info["sku"] == "SKU1"
        assert info["product_name"] == "Chair"
        assert "ergonomic" in info["pitch_excerpt"]

    def test_unknown_campaign_returns_empty(self):
        info = get_product_info(_conn(), campaign_id="nope", env="LIVE")
        assert info == {"sku": None, "product_name": None, "pitch_excerpt": None}


@pytest.mark.asyncio
class TestValidation:
    async def test_missing_feedback_422(self):
        with pytest.raises(HTTPException) as exc:
            await validate_decision_feedback(
                _BridgeStub(), feedback=None, handles=["a"],
                sku="SKU1", env="LIVE", action="approve",
            )
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "decision_feedback_required"

    async def test_missing_tags_422_lists_handles(self):
        fb = _feedback(shared_tags=[], shared_comment="reason")
        with pytest.raises(HTTPException) as exc:
            await validate_decision_feedback(
                _BridgeStub(), feedback=fb, handles=["a", "b"],
                sku="SKU1", env="LIVE", action="approve",
            )
        assert exc.value.detail["code"] == "decision_tags_required"
        assert exc.value.detail["handles"] == ["a", "b"]

    async def test_override_tags_satisfy_requirement(self):
        fb = _feedback(
            shared_tags=[],
            shared_comment="reason",
            per_kol_overrides={"a": {"tags": ["tone_match"], "comment": None}},
        )
        await validate_decision_feedback(
            _BridgeStub(), feedback=fb, handles=["a"],
            sku="SKU1", env="LIVE", action="approve",
        )

    async def test_comment_required_early_phase(self):
        fb = _feedback(shared_tags=["tone_match"], shared_comment=None)
        with pytest.raises(HTTPException) as exc:
            await validate_decision_feedback(
                _BridgeStub(comment_required=True), feedback=fb, handles=["a"],
                sku="SKU1", env="LIVE", action="approve",
            )
        assert exc.value.detail["code"] == "decision_comment_required"

    async def test_comment_optional_after_threshold(self):
        fb = _feedback(shared_tags=["tone_match"], shared_comment=None)
        await validate_decision_feedback(
            _BridgeStub(comment_required=False), feedback=fb, handles=["a"],
            sku="SKU1", env="LIVE", action="approve",
        )

    async def test_bridge_down_degrades_comment_requirement(self):
        fb = _feedback(shared_tags=["tone_match"], shared_comment=None)
        await validate_decision_feedback(
            _BridgeStub(fail=True), feedback=fb, handles=["a"],
            sku="SKU1", env="LIVE", action="approve",
        )

    async def test_empty_handles_skips_validation(self):
        await validate_decision_feedback(
            _BridgeStub(), feedback=None, handles=[],
            sku="SKU1", env="LIVE", action="approve",
        )

    async def test_kill_switch_disables_validation(self, monkeypatch):
        from app import discovery_feedback as df

        monkeypatch.setattr(df, "_feedback_required", lambda: False)
        await validate_decision_feedback(
            _BridgeStub(), feedback=None, handles=["a"],
            sku="SKU1", env="LIVE", action="approve",
        )

    async def test_unknown_tag_422_lists_invalid(self):
        fb = _feedback(shared_tags=["stale_tag"], shared_comment="reason")
        with pytest.raises(HTTPException) as exc:
            await validate_decision_feedback(
                _BridgeStub(), feedback=fb, handles=["a"],
                sku="SKU1", env="LIVE", action="approve",
            )
        assert exc.value.detail["code"] == "decision_tags_invalid"
        assert exc.value.detail["invalid_tags"] == ["stale_tag"]

    async def test_override_tags_also_vocabulary_checked(self):
        fb = _feedback(
            shared_tags=["tone_match"],
            shared_comment="reason",
            per_kol_overrides={"b": {"tags": ["bogus"], "comment": None}},
        )
        with pytest.raises(HTTPException) as exc:
            await validate_decision_feedback(
                _BridgeStub(), feedback=fb, handles=["a", "b"],
                sku="SKU1", env="LIVE", action="approve",
            )
        assert exc.value.detail["code"] == "decision_tags_invalid"
        assert exc.value.detail["invalid_tags"] == ["bogus"]

    async def test_vocabulary_unavailable_skips_strict_check(self):
        # Bridge down → vocabulary unknown → tags accepted as-is (the bridge
        # will normalize on capture); comment requirement also degrades.
        fb = _feedback(shared_tags=["anything"], shared_comment=None)
        await validate_decision_feedback(
            _BridgeStub(fail=True), feedback=fb, handles=["a"],
            sku="SKU1", env="LIVE", action="approve",
        )


class TestResolvePerKol:
    def test_shared_values_apply(self):
        fb = _feedback(shared_tags=["tone_match"], shared_comment="great fit")
        rows = [{"identity_id": 1, "handle": "a"}, {"identity_id": 2, "handle": "b"}]
        out = resolve_per_kol_decisions(feedback=fb, rows=rows)
        assert all(d["tags"] == ["tone_match"] for d in out)
        assert all(d["comment"] == "great fit" for d in out)

    def test_override_wins_for_its_handle(self):
        fb = _feedback(
            shared_tags=["tone_match"],
            shared_comment="shared",
            per_kol_overrides={"b": {"tags": ["audience_fit"], "comment": "special"}},
        )
        rows = [{"identity_id": 1, "handle": "a"}, {"identity_id": 2, "handle": "b"}]
        out = resolve_per_kol_decisions(feedback=fb, rows=rows)
        assert out[0]["tags"] == ["tone_match"]
        assert out[1]["tags"] == ["audience_fit"]
        assert out[1]["comment"] == "special"


@pytest.mark.asyncio
class TestRecordDecisionsSafe:
    async def test_success_passes_through(self):
        bridge = _BridgeStub()
        out = await record_decisions_safe(
            bridge, _conn(),
            campaign_id="camp-1", env="LIVE", action="approve",
            decided_by="web:op", actor_user_id=1,
            decisions=[{"identity_id": 1, "tags": ["tone_match"], "comment": "x"}],
            product_info={"sku": "SKU1", "product_name": "Chair", "pitch_excerpt": "p"},
        )
        assert out["recorded"] == 1
        assert bridge.recorded[0]["sku"] == "SKU1"

    async def test_failure_degrades_and_audits_replay_body(self):
        conn = _conn()
        out = await record_decisions_safe(
            _BridgeStub(fail=True), conn,
            campaign_id="camp-1", env="LIVE", action="remove",
            decided_by="web:op", actor_user_id=1,
            decisions=[{"identity_id": 1, "tags": ["other"], "comment": None}],
            product_info={"sku": "SKU1"},
            max_attempts=2, retry_delay_sec=0,
        )
        assert out["recorded"] == 0 and "error" in out
        row = conn.execute(
            "SELECT action, payload_json FROM audit_log ORDER BY id DESC LIMIT 1",
        ).fetchone()
        assert row["action"] == "learning.shortlist_decision_failed"
        import json as _json

        payload = _json.loads(row["payload_json"])
        # Full body preserved so the sample can be replayed against the bridge.
        replay = payload["replay_body"]
        assert replay["campaign_id"] == "camp-1"
        assert replay["action"] == "remove"
        assert replay["decisions"][0]["identity_id"] == 1

    async def test_transient_failure_retried_then_succeeds(self):
        bridge = _BridgeStub(fail_times=1)
        out = await record_decisions_safe(
            bridge, _conn(),
            campaign_id="camp-1", env="LIVE", action="approve",
            decided_by="web:op", actor_user_id=1,
            decisions=[{"identity_id": 1, "tags": ["tone_match"], "comment": "x"}],
            product_info={"sku": "SKU1"},
            max_attempts=3, retry_delay_sec=0,
        )
        assert out["recorded"] == 1
        assert bridge.record_calls == 2


@pytest.mark.asyncio
class TestReplayShortlistCapture:
    async def test_list_failed_captures(self):
        conn = _conn()
        await record_decisions_safe(
            _BridgeStub(fail=True), conn,
            campaign_id="camp-1", env="LIVE", action="approve",
            decided_by="web:op", actor_user_id=1,
            decisions=[{"identity_id": 1, "tags": ["tone_match"], "comment": "x"}],
            product_info={"sku": "SKU1"},
            max_attempts=1, retry_delay_sec=0,
        )
        items = list_failed_shortlist_captures(conn)
        assert len(items) == 1
        assert items[0]["audit_id"] >= 1
        assert items[0]["capture_action"] == "approve"
        assert items[0]["sku"] == "SKU1"

    async def test_replay_writes_events_and_marks_audit(self):
        conn = _conn()
        await record_decisions_safe(
            _BridgeStub(fail=True), conn,
            campaign_id="camp-1", env="LIVE", action="remove",
            decided_by="web:op", actor_user_id=1,
            decisions=[{"identity_id": 2, "tags": ["other"], "comment": None}],
            product_info={"sku": "SKU1"},
            max_attempts=1, retry_delay_sec=0,
        )
        audit_id = list_failed_shortlist_captures(conn)[0]["audit_id"]
        bridge = _BridgeStub()
        out = await replay_shortlist_capture(
            bridge, conn, audit_id=audit_id, actor_user_id=9,
        )
        assert out["already_replayed"] is False
        assert out["recorded"] == 1
        assert list_failed_shortlist_captures(conn) == []
        again = await replay_shortlist_capture(
            bridge, conn, audit_id=audit_id, actor_user_id=9,
        )
        assert again["already_replayed"] is True


@pytest.mark.asyncio
class TestLearnedCriteriaBriefSection:
    async def test_section_contains_both_levels(self):
        section = await learned_criteria_brief_section(
            _BridgeStub(), sku="SKU1", env="LIVE",
        )
        assert "# learned_discovery_criteria" in section
        assert "SPU rules" in section
        assert "Category rules" in section
        assert "category=chair" in section

    async def test_bridge_down_returns_empty(self):
        section = await learned_criteria_brief_section(
            _BridgeStub(fail=True), sku="SKU1", env="LIVE",
        )
        assert section == ""

    async def test_no_sku_returns_empty(self):
        section = await learned_criteria_brief_section(
            _BridgeStub(), sku=None, env="LIVE",
        )
        assert section == ""

    async def test_toggle_off_returns_empty(self, monkeypatch):
        from app import learned_criteria as lc

        settings = lc.get_settings()
        monkeypatch.setattr(settings, "discovery_learned_criteria", False)
        section = await learned_criteria_brief_section(
            _BridgeStub(), sku="SKU1", env="LIVE",
        )
        assert section == ""
