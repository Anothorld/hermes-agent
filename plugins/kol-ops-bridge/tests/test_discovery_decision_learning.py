"""Tests for the shortlist decision learning channel.

Covers: tag vocabulary (seed / normalize / propose / decide), decision event
capture with frozen KOL snapshots, comment-required policy, product category
map precedence, dynamic ``discovery_criteria:*`` policy scopes, distill +
mining jobs (LLM mocked), and approval merge.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def pkg(cal_db, bridge_pkg):  # noqa: ARG001 — cal_db points CAL at a temp DB
    return bridge_pkg


def _mk_identity(pkg, handle: str = "@tester", env: str = "LIVE") -> int:
    iid = pkg.cal.upsert_identity(primary_handle=handle, platform="instagram", env=env)
    assert isinstance(iid, int)
    return iid


def _record_one(
    pkg,
    conn,
    *,
    identity_id: int,
    action: str = "remove",
    tags=None,
    comment: str = "粉丝画像太低龄化",
    sku: str = "SKU1",
    campaign_id: str = "camp-1",
):
    return pkg.discovery_decision_learning.record_shortlist_decisions(
        conn,
        campaign_id=campaign_id,
        env="LIVE",
        action=action,
        decided_by="web:op@example.com",
        decisions=[{
            "identity_id": identity_id,
            "tags": tags or ["fake_followers"],
            "comment": comment,
        }],
        operator_user_id=7,
        sku=sku,
        product_name="Test Chair",
        pitch_excerpt="ergonomic office chair",
    )


# ---------------------------------------------------------------------------
# Tag vocabulary
# ---------------------------------------------------------------------------


class TestDecisionTags:
    def test_seed_tags_inserted_once(self, pkg):
        with pkg.cal._connect() as conn:
            first = pkg.discovery_decision_tags.ensure_seed_tags(conn)
            second = pkg.discovery_decision_tags.ensure_seed_tags(conn)
        assert first == len(pkg.discovery_decision_tags.SEED_TAGS)
        assert second == 0

    def test_action_filter_includes_any_scope(self, pkg):
        with pkg.cal._connect() as conn:
            tags = {t["tag"] for t in pkg.discovery_decision_tags.list_tags(conn, action="remove")}
        assert "fake_followers" in tags
        assert "other" in tags  # action_scope=any
        assert "tone_match" not in tags  # approve-only

    def test_normalize_unknown_maps_to_other(self, pkg):
        with pkg.cal._connect() as conn:
            out = pkg.discovery_decision_tags.normalize_decision_tags(
                conn, ["fake_followers", "BOGUS", ""], action="remove",
            )
        assert out == ["fake_followers", "other"]

    def test_normalize_empty_defaults_other(self, pkg):
        with pkg.cal._connect() as conn:
            assert pkg.discovery_decision_tags.normalize_decision_tags(
                conn, None, action="approve",
            ) == ["other"]

    def test_propose_then_approve_activates(self, pkg):
        with pkg.cal._connect() as conn:
            res = pkg.discovery_decision_tags.propose_tag(
                conn, tag="audience_too_young", label_zh="受众太低龄",
                action_scope="remove", evidence=["粉丝太小"],
            )
            assert res["created"] is True
            # not selectable while proposed
            active = pkg.discovery_decision_tags.active_tag_set(conn, action="remove")
            assert "audience_too_young" not in active
            pkg.discovery_decision_tags.decide_proposed_tag(
                conn, tag="audience_too_young", decision="approved",
            )
            active = pkg.discovery_decision_tags.active_tag_set(conn, action="remove")
            assert "audience_too_young" in active

    def test_rejected_proposal_not_reproposed(self, pkg):
        with pkg.cal._connect() as conn:
            pkg.discovery_decision_tags.propose_tag(
                conn, tag="noise_reason", label_zh="噪音", action_scope="any",
            )
            pkg.discovery_decision_tags.decide_proposed_tag(
                conn, tag="noise_reason", decision="rejected",
            )
            res = pkg.discovery_decision_tags.propose_tag(
                conn, tag="noise_reason", label_zh="噪音", action_scope="any",
            )
            assert res["created"] is False

    def test_invalid_slug_rejected(self, pkg):
        with pkg.cal._connect() as conn:
            with pytest.raises(ValueError):
                pkg.discovery_decision_tags.propose_tag(
                    conn, tag="Bad Tag!", label_zh="x",
                )


# ---------------------------------------------------------------------------
# Decision events + snapshot
# ---------------------------------------------------------------------------


class TestDecisionEvents:
    def test_record_writes_event_with_snapshot(self, pkg):
        iid = _mk_identity(pkg)
        pkg.cal.write_facts(
            identity_id=iid, campaign_id=None, namespace="identity",
            facts={"identity.creator_type": "lifestyle", "identity.followers": 250_000},
            source="manual", env="LIVE",
        )
        with pkg.cal._connect() as conn:
            out = _record_one(pkg, conn, identity_id=iid)
            assert out["recorded"] == 1 and not out["skipped"]
            events = pkg.discovery_decision_learning.list_decision_events(
                conn, env="LIVE", sku="SKU1",
            )
        payload = events[0]["payload"]
        assert payload["action"] == "remove"
        assert payload["reason_tags"] == ["fake_followers"]
        assert payload["comment"] == "粉丝画像太低龄化"
        facts = payload["kol_snapshot"]["facts"]
        assert facts["identity.creator_type"] == "lifestyle"
        assert payload["kol_snapshot"]["identity"]["primary_handle"] == "@tester"

    def test_unknown_identity_skipped_not_raised(self, pkg):
        with pkg.cal._connect() as conn:
            out = pkg.discovery_decision_learning.record_shortlist_decisions(
                conn,
                campaign_id="camp-1", env="LIVE", action="remove",
                decided_by="web:x",
                decisions=[{"identity_id": 99999, "tags": ["other"], "comment": ""}],
            )
        assert out["recorded"] == 0
        assert out["skipped"][0]["reason"] == "identity_not_found"

    def test_invalid_action_raises(self, pkg):
        with pkg.cal._connect() as conn:
            with pytest.raises(ValueError):
                pkg.discovery_decision_learning.record_shortlist_decisions(
                    conn, campaign_id="c", env="LIVE", action="bogus",
                    decided_by="x", decisions=[],
                )

    def test_comment_required_until_threshold(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_COMMENT_MIN_SAMPLES", "2")
        iid = _mk_identity(pkg)
        with pkg.cal._connect() as conn:
            req = pkg.discovery_decision_learning.feedback_requirements(
                conn, env="LIVE", sku="SKU1",
            )
            assert req["comment_required"] is True
            _record_one(pkg, conn, identity_id=iid)
            _record_one(pkg, conn, identity_id=iid, action="approve",
                        tags=["tone_match"], campaign_id="camp-2")
            req = pkg.discovery_decision_learning.feedback_requirements(
                conn, env="LIVE", sku="SKU1",
            )
        assert req["sku_sample_count"] == 2
        assert req["comment_required"] is False


# ---------------------------------------------------------------------------
# Product category map
# ---------------------------------------------------------------------------


class TestProductCategoryMap:
    def test_llm_does_not_overwrite_operator(self, pkg):
        with pkg.cal._connect() as conn:
            pkg.discovery_decision_learning.set_product_category(
                conn, sku="SKU1", category="ergonomic_chair",
                source="operator", updated_by="console:op",
            )
            res = pkg.discovery_decision_learning.set_product_category(
                conn, sku="SKU1", category="office_chair",
                source="llm", updated_by="learning:job",
            )
            assert res["skipped"] is True
            assert pkg.discovery_decision_learning.get_category_for_sku(
                conn, sku="SKU1",
            ) == "ergonomic_chair"

    def test_operator_overwrites_llm(self, pkg):
        with pkg.cal._connect() as conn:
            pkg.discovery_decision_learning.set_product_category(
                conn, sku="SKU2", category="sofa", source="llm", updated_by="job",
            )
            pkg.discovery_decision_learning.set_product_category(
                conn, sku="SKU2", category="sofa_bed", source="operator", updated_by="op",
            )
            assert pkg.discovery_decision_learning.get_category_for_sku(
                conn, sku="SKU2",
            ) == "sofa_bed"


# ---------------------------------------------------------------------------
# Dynamic discovery_criteria:* policy scopes
# ---------------------------------------------------------------------------


class TestDiscoveryCriteriaScopes:
    def test_scope_builder_and_validation(self, pkg):
        pol = pkg.policies
        scope = pol.discovery_criteria_scope("spu", "SKU1")
        assert scope == "discovery_criteria:spu:SKU1"
        assert pol.is_discovery_criteria_scope(scope)
        assert not pol.is_discovery_criteria_scope("discovery_criteria:bogus:x")
        assert not pol.is_discovery_criteria_scope("reply_learning")

    def test_cjk_key_slugs_deterministically(self, pkg):
        pol = pkg.policies
        a = pol.discovery_criteria_scope("category", "家具")
        b = pol.discovery_criteria_scope("category", "家具")
        assert a == b
        assert pol.is_discovery_criteria_scope(a)

    def test_put_get_env_scoped(self, pkg):
        pol = pkg.policies
        scope = pol.discovery_criteria_scope("spu", "SKU1")
        with pkg.cal._connect() as conn:
            row = pol.put_policy(
                conn, scope=scope, content_md="# learned v1",
                updated_by="test", env="LIVE",
            )
            assert row["version"] == 1 and row["env"] == "LIVE"
            assert pol.get_policy(conn, scope=scope, env="TEST") is None
            got = pol.get_policy(conn, scope=scope, env="LIVE")
            assert got["content_md"] == "# learned v1"

    def test_invalid_scope_still_rejected(self, pkg):
        with pkg.cal._connect() as conn:
            with pytest.raises(ValueError):
                pkg.policies.put_policy(
                    conn, scope="not_a_scope", content_md="x", updated_by="t",
                )


# ---------------------------------------------------------------------------
# Distill job (LLM mocked)
# ---------------------------------------------------------------------------


def _seed_decisions(pkg, n: int, *, sku: str = "SKU1") -> None:
    with pkg.cal._connect() as conn:
        for i in range(n):
            iid = _mk_identity(pkg, handle=f"@kol{i}_{sku}")
            _record_one(pkg, conn, identity_id=iid, sku=sku, campaign_id=f"camp-{sku}")


class TestDiscoveryDistill:
    def test_below_threshold_skips(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_LEARNING_BATCH_SIZE", "5")
        _seed_decisions(pkg, 2)
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.propose_discovery_learning_approval(
                conn, env="LIVE", updated_by="test",
            )
        assert out["skipped"] is True

    def test_proposes_pending_approval_and_reserves_events(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_LEARNING_BATCH_SIZE", "2")
        monkeypatch.setattr(
            pkg.learning_discovery.learning_llm,
            "invoke_learning_llm",
            lambda prompt: "## Approved discovery learning\n- 偏好精致白领受众",
        )
        _seed_decisions(pkg, 3)
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.propose_discovery_learning_approval(
                conn, env="LIVE", updated_by="test",
            )
            assert out["proposed_count"] >= 1
            pending = pkg.learning_discovery.list_pending_discovery_proposals(
                conn, env="LIVE",
            )
            spu_proposal = next(
                p for p in pending
                if p["value"].get("scope") == "discovery_criteria:spu:SKU1"
            )
            assert spu_proposal["value"].get("sample_identity_count") == 3
            scopes = {p["value"]["scope"] for p in pending}
            assert "discovery_criteria:spu:SKU1" in scopes
            # Re-running must not double-propose while pending exists.
            again = pkg.learning_discovery.propose_discovery_learning_approval(
                conn, env="LIVE", updated_by="test",
            )
            assert again.get("skipped") is True

    def test_apply_approved_merges_policy(self, pkg):
        proposal = {
            "scope": "discovery_criteria:spu:SKU1",
            "proposed_markdown": "## Approved discovery learning\n- bullet",
            "title": "Discovery learning (spu:SKU1, LIVE)",
        }
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.apply_approved_discovery_proposal(
                conn, env="LIVE", proposal=proposal, updated_by="approval:op",
            )
            assert out["version"] == 1
            row = pkg.policies.get_policy(
                conn, scope="discovery_criteria:spu:SKU1", env="LIVE",
            )
            assert "bullet" in row["content_md"]

    def test_apply_approved_strips_background_notes_from_policy(self, pkg):
        proposal = {
            "scope": "discovery_criteria:spu:SKU1",
            "proposed_markdown": (
                "## Approved discovery learning\n"
                "### Preferred KOL profile\n"
                "- approve creators with strong engagement\n"
                "### 背景说明\n"
                "- 批次大小：总共 **31** 个决策。\n"
                "- 样本中观察到的行动组合：**8 批准 / 22 移除**\n"
            ),
            "title": "Discovery learning (spu:SKU1, LIVE)",
        }
        with pkg.cal._connect() as conn:
            pkg.learning_discovery.apply_approved_discovery_proposal(
                conn, env="LIVE", proposal=proposal, updated_by="approval:op",
            )
            row = pkg.policies.get_policy(
                conn, scope="discovery_criteria:spu:SKU1", env="LIVE",
            )
            content = row["content_md"]
            assert "strong engagement" in content
            assert "背景说明" not in content
            assert "批次大小" not in content

    def test_apply_rejects_bad_scope(self, pkg):
        with pkg.cal._connect() as conn:
            with pytest.raises(ValueError):
                pkg.learning_discovery.apply_approved_discovery_proposal(
                    conn, env="LIVE",
                    proposal={"scope": "reply_learning", "proposed_markdown": "x"},
                    updated_by="t",
                )

    def test_distill_prompt_includes_rejection_feedback(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_LEARNING_BATCH_SIZE", "2")
        scope = "discovery_criteria:spu:SKU1"
        iid = _mk_identity(pkg, handle="@rejectanchor")
        pkg.cal.write_event(
            identity_id=iid, campaign_id=None,
            event_type="discovery_proposal_rejected", goal=None, lane="meta",
            actor="approval:op",
            payload={
                "scope": scope,
                "note": "太激进，不要排除小众风格",
                "tags": ["too_aggressive"],
                "rejected_markdown": "## Approved discovery learning\n- 排除所有小众风格",
            },
            env="LIVE",
        )
        captured: dict[str, str] = {}

        def _fake_llm(prompt: str) -> str:
            captured["prompt"] = prompt
            return "## Approved discovery learning\n- ok"

        monkeypatch.setattr(
            pkg.learning_discovery.learning_llm, "invoke_learning_llm", _fake_llm,
        )
        _seed_decisions(pkg, 2)
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.propose_discovery_learning_approval(
                conn, env="LIVE", updated_by="test",
            )
        assert out["proposed_count"] >= 1
        assert "PREVIOUSLY REJECTED PROPOSALS" in captured["prompt"]
        assert "太激进" in captured["prompt"]

    def test_count_decision_samples_uses_sql(self, pkg):
        _seed_decisions(pkg, 3)
        _seed_decisions(pkg, 1, sku="SKU_OTHER")
        with pkg.cal._connect() as conn:
            assert pkg.discovery_decision_learning.count_decision_samples(
                conn, env="LIVE", sku="SKU1",
            ) == 3
            assert pkg.discovery_decision_learning.count_decision_samples(
                conn, env="LIVE", sku="SKU_OTHER",
            ) == 1
            assert pkg.discovery_decision_learning.count_decision_samples(
                conn, env="LIVE", sku="SKU_NONE",
            ) == 0

    def test_overview_stats_reports_group_progress(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_LEARNING_BATCH_SIZE", "5")
        _seed_decisions(pkg, 2)
        with pkg.cal._connect() as conn:
            stats = pkg.learning_discovery.discovery_overview_stats(conn, env="LIVE")
        assert stats["fresh_decisions"] == 2
        spu = next(
            g for g in stats["groups"]
            if g["group_kind"] == "spu" and g["group_key"] == "SKU1"
        )
        assert spu["fresh_samples"] == 2
        assert spu["ready_for_distill"] is False
        assert spu["has_pending_proposal"] is False

    def test_merge_preview_supports_discovery_scope(self, pkg):
        scope = "discovery_criteria:spu:SKU1"
        with pkg.cal._connect() as conn:
            pkg.policies.put_policy(
                conn, scope=scope, content_md="baseline", updated_by="t", env="LIVE",
            )
            preview = pkg.learning_distill.preview_policy_merge_from_proposal(
                conn,
                env="LIVE",
                proposal={
                    "scope": scope,
                    "proposed_markdown": "## Approved discovery learning\n- new bullet",
                },
            )
        section = preview["sections"]["discovery"]
        assert section["scope"] == scope
        assert "baseline" in section["current_md"]
        assert "new bullet" in section["merged_md"]

    def test_merge_preview_keeps_background_in_proposal_only(self, pkg):
        scope = "discovery_criteria:spu:SKU1"
        proposed = (
            "## Approved discovery learning\n"
            "- rule bullet\n"
            "### 背景说明\n"
            "- batch meta\n"
        )
        with pkg.cal._connect() as conn:
            preview = pkg.learning_distill.preview_policy_merge_from_proposal(
                conn,
                env="LIVE",
                proposal={"scope": scope, "proposed_markdown": proposed},
            )
        section = preview["sections"]["discovery"]
        assert "背景说明" in section["proposed_section_md"]
        assert "batch meta" in section["proposed_section_md"]
        assert "背景说明" not in section["policy_merge_section_md"]
        assert "背景说明" not in section["merged_md"]

    def test_brief_section_prefers_spu_then_category(self, pkg):
        with pkg.cal._connect() as conn:
            pkg.discovery_decision_learning.set_product_category(
                conn, sku="SKU1", category="chair", source="llm", updated_by="t",
            )
            pkg.policies.put_policy(
                conn, scope="discovery_criteria:spu:SKU1",
                content_md="SPU criteria", updated_by="t", env="LIVE",
            )
            pkg.policies.put_policy(
                conn, scope="discovery_criteria:category:chair",
                content_md="Category criteria", updated_by="t", env="LIVE",
            )
            out = pkg.learning_discovery.build_learned_discovery_criteria(
                conn, env="LIVE", sku="SKU1",
            )
        assert out["spu_md"] == "SPU criteria"
        assert out["category_md"] == "Category criteria"
        assert out["category"] == "chair"


# ---------------------------------------------------------------------------
# Tag mining + category inference (LLM mocked)
# ---------------------------------------------------------------------------


class TestMiningJobs:
    def test_mine_discovery_tags_creates_proposals(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_TAG_MINE_MIN_COUNT", "2")
        _seed_decisions(pkg, 3)
        mined = [{
            "tag": "audience_too_young",
            "label_zh": "受众太低龄",
            "action_scope": "remove",
            "count": 3,
            "examples": ["粉丝画像太低龄化"],
        }]
        monkeypatch.setattr(
            pkg.learning_discovery.learning_llm,
            "invoke_learning_llm",
            lambda prompt: json.dumps(mined),
        )
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.mine_discovery_tags(conn, env="LIVE")
            assert out["proposed_tags"] == ["audience_too_young"]
            proposed = pkg.discovery_decision_tags.list_tags(conn, status="proposed")
        assert any(t["tag"] == "audience_too_young" for t in proposed)

    def test_mine_ignores_unverifiable_examples(self, pkg, monkeypatch):
        """LLM-claimed counts with fabricated examples must not become proposals."""
        monkeypatch.setenv("KOL_DISCOVERY_TAG_MINE_MIN_COUNT", "2")
        _seed_decisions(pkg, 3)
        mined = [{
            "tag": "fabricated_reason",
            "label_zh": "捏造的原因",
            "action_scope": "remove",
            "count": 99,
            "examples": ["这条评论从未出现过"],
        }]
        monkeypatch.setattr(
            pkg.learning_discovery.learning_llm,
            "invoke_learning_llm",
            lambda prompt: json.dumps(mined),
        )
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.mine_discovery_tags(conn, env="LIVE")
        assert out["proposed_tags"] == []
        assert any(
            i.get("reason") == "examples_not_found_in_comments"
            for i in out["ignored"]
        )

    def test_mine_skips_when_too_few_comments(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_TAG_MINE_MIN_COUNT", "5")
        _seed_decisions(pkg, 1)
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.mine_discovery_tags(conn, env="LIVE")
        assert out["skipped"] is True

    def test_infer_categories_writes_llm_rows(self, pkg, monkeypatch):
        _seed_decisions(pkg, 1, sku="SKU9")
        monkeypatch.setattr(
            pkg.learning_discovery.learning_llm,
            "invoke_learning_llm",
            lambda prompt: json.dumps({"SKU9": {"category": "sofa", "confidence": 0.9}}),
        )
        with pkg.cal._connect() as conn:
            out = pkg.learning_discovery.infer_missing_product_categories(
                conn, env="LIVE", updated_by="job",
            )
            assert out["written_count"] == 1
            assert pkg.discovery_decision_learning.get_category_for_sku(
                conn, sku="SKU9",
            ) == "sofa"


# ---------------------------------------------------------------------------
# Job registry wiring
# ---------------------------------------------------------------------------


class TestJobWiring:
    def test_jobs_registered_in_nightly_suite(self, pkg):
        jobs = pkg.learning_jobs
        assert jobs.JOB_APPLY_DISCOVERY_POLICY in jobs.JOB_SUITES["nightly"]
        assert jobs.JOB_MINE_DISCOVERY_TAGS in jobs.JOB_SUITES["nightly"]
        assert jobs.JOB_APPLY_DISCOVERY_POLICY in jobs.JOB_SUITES["distill"]
        assert set(jobs.JOB_SUITES["nightly"]).issubset(set(jobs.ALL_JOBS))

    def test_dry_run_reports_groups(self, pkg, monkeypatch):
        monkeypatch.setenv("KOL_DISCOVERY_LEARNING_BATCH_SIZE", "10")
        _seed_decisions(pkg, 2)
        with pkg.cal._connect() as conn:
            out = pkg.learning_jobs.run_single_job(
                conn,
                job_name=pkg.learning_jobs.JOB_APPLY_DISCOVERY_POLICY,
                env="LIVE",
                triggered_by="test",
                dry_run=True,
            )
        assert out["status"] == "ok"
        assert out["output"]["groups"].get("spu:SKU1") == 2
