"""Tests for ad_detector module."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PKG = "cs_ops_bridge_ad_detector_test"


def _load_module():
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_PLUGIN_ROOT)]
        sys.modules[_PKG] = pkg
    full = f"{_PKG}.ad_detector"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(
        full,
        _PLUGIN_ROOT / "ad_detector.py",
        submodule_search_locations=[str(_PLUGIN_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ad():
    return _load_module()


# ─── detect_ad_email ───────────────────────────────────────────────

class TestDetectAdEmail:
    def test_collaboration_keyword(self, ad):
        assert ad.detect_ad_email(
            subject="Partnership opportunity",
            body="We'd love to collaborate with your brand",
        )

    def test_collaborate_in_body(self, ad):
        assert ad.detect_ad_email(
            subject="Hello",
            body="Would you like to collaborate on a campaign?",
        )

    def test_proposal_keyword(self, ad):
        assert ad.detect_ad_email(
            subject="SEO proposal for your website",
            body="",
        )

    def test_tariff_keyword(self, ad):
        assert ad.detect_ad_email(
            subject="Updated tariff rates for shipping",
            body="Please review the attached tariff document",
        )

    def test_guest_post_with_space(self, ad):
        assert ad.detect_ad_email(
            subject="Guest post opportunity",
            body="",
        )

    def test_guestpost_one_word(self, ad):
        assert ad.detect_ad_email(
            subject="Guestpost inquiry",
            body="",
        )

    def test_partnership_keyword(self, ad):
        assert ad.detect_ad_email(
            subject="Strategic partnership proposal",
            body="",
        )

    def test_seo_keyword(self, ad):
        assert ad.detect_ad_email(
            subject="Improve your SEO ranking",
            body="",
        )

    def test_seo_case_insensitive(self, ad):
        assert ad.detect_ad_email(
            subject="Seo audit report",
            body="",
        )

    def test_seo_uppercase(self, ad):
        assert ad.detect_ad_email(
            subject="SEO optimization services",
            body="",
        )

    def test_keyword_in_content_preview(self, ad):
        assert ad.detect_ad_email(
            subject="Hello",
            content_preview="We are reaching out about a potential partnership...",
        )

    def test_no_ad_normal_customer_email(self, ad):
        assert not ad.detect_ad_email(
            subject="Re: Povison sofa delivery timeline",
            body="Hi, when will my order arrive? Order #1234567890",
        )

    def test_no_ad_product_inquiry(self, ad):
        assert not ad.detect_ad_email(
            subject="Question about Aurora Power Sofa Bed dimensions",
            body="What are the dimensions of this sofa bed?",
        )

    def test_no_ad_logistics_inquiry(self, ad):
        assert not ad.detect_ad_email(
            subject="Where is my order?",
            body="Tracking number 1Z999AA10123456789",
        )

    def test_no_ad_empty_fields(self, ad):
        assert not ad.detect_ad_email(subject="", body="", content_preview="")

    def test_no_ad_only_keyword_fragment_seoul(self, ad):
        # "Seoul" should NOT match "seo" because of word boundary.
        assert not ad.detect_ad_email(
            subject="Delivery to Seoul, Korea",
            body="",
        )

    def test_ad_in_subject_not_body(self, ad):
        assert ad.detect_ad_email(
            subject="Collaboration opportunity with our agency",
            body="Dear Povison team, we found your website and...",
        )

    def test_ad_multiple_keywords(self, ad):
        assert ad.detect_ad_email(
            subject="SEO and collaboration proposal",
            body="We offer guest post services and partnership opportunities.",
        )


# ─── detect_ad_from_info ───────────────────────────────────────────

class TestDetectAdFromInfo:
    def test_sio_info_with_subject(self, ad):
        info = {
            "email_subject": "Partnership proposal for Povison",
            "content_preview": "We would like to collaborate...",
            "from": "agency@example.com",
        }
        assert ad.detect_ad_from_info(info)

    def test_sio_info_with_preview_only(self, ad):
        info = {
            "email_subject": "Hello",
            "content_preview": "We are an SEO agency reaching out...",
            "from": "",
        }
        assert ad.detect_ad_from_info(info)

    def test_rest_info_empty_fields(self, ad):
        info = {
            "email_subject": "",
            "content_preview": "",
        }
        assert not ad.detect_ad_from_info(info)

    def test_normal_customer_email(self, ad):
        info = {
            "email_subject": "Re: Barrett sofa fabric question",
            "content_preview": "Hi, is the velvet available in dark gray?",
            "from": "customer@gmail.com",
        }
        assert not ad.detect_ad_from_info(info)

    def test_sender_name_with_keyword(self, ad):
        info = {
            "email_subject": "Hello",
            "content_preview": "",
            "from": "SEO Agency <noreply@example.com>",
        }
        assert ad.detect_ad_from_info(info)


# ─── parse_rest_last_msg_content ──────────────────────────────────

class TestParseRestLastMsgContent:
    def test_valid_json_string(self, ad):
        row = {
            "lastMsgContent": json.dumps({
                "emailSubject": "Collaboration opportunity",
                "content": "We would like to partner with you.",
            }),
        }
        subject, content = ad.parse_rest_last_msg_content(row)
        assert subject == "Collaboration opportunity"
        assert "partner" in content

    def test_dict_already_parsed(self, ad):
        row = {
            "lastMsgContent": {
                "emailSubject": "SEO proposal",
                "content": "Improve your rankings.",
            },
        }
        subject, content = ad.parse_rest_last_msg_content(row)
        assert subject == "SEO proposal"
        assert content == "Improve your rankings."

    def test_empty_last_msg_content(self, ad):
        row = {}
        subject, content = ad.parse_rest_last_msg_content(row)
        assert subject == ""
        assert content == ""

    def test_none_last_msg_content(self, ad):
        row = {"lastMsgContent": None}
        subject, content = ad.parse_rest_last_msg_content(row)
        assert subject == ""
        assert content == ""

    def test_invalid_json_string(self, ad):
        row = {"lastMsgContent": "not json{"}
        subject, content = ad.parse_rest_last_msg_content(row)
        assert subject == ""
        assert content == ""

    def test_missing_email_subject_key(self, ad):
        row = {"lastMsgContent": json.dumps({"content": "hello"})}
        subject, content = ad.parse_rest_last_msg_content(row)
        assert subject == ""
        assert content == "hello"

    def test_real_world_session_row(self, ad):
        row = {
            "lastMsgContent": json.dumps({
                "emailSubject": "POVISON SF8261E220 Cotton Linen Curved Sofa",
                "content": "",
            }),
        }
        subject, content = ad.parse_rest_last_msg_content(row)
        assert "POVISON" in subject
        assert content == ""


# ─── has_ad_tag ────────────────────────────────────────────────────

class TestHasAdTag:
    def test_chinese_ad_tag(self, ad):
        row = {"subSessionTags": ["广告", "待客户回复"]}
        assert ad.has_ad_tag(row)

    def test_english_advertising_tag(self, ad):
        row = {"subSessionTags": ["Advertising", "Email"]}
        assert ad.has_ad_tag(row)

    def test_marketing_tag(self, ad):
        row = {"subSessionTags": ["Marketing"]}
        assert ad.has_ad_tag(row)

    def test_no_ad_tags(self, ad):
        row = {"subSessionTags": ["待客户回复", "Product inquiry产品咨询"]}
        assert not ad.has_ad_tag(row)

    def test_empty_tags(self, ad):
        row = {"subSessionTags": []}
        assert not ad.has_ad_tag(row)

    def test_none_tags(self, ad):
        row = {"subSessionTags": None}
        assert not ad.has_ad_tag(row)

    def test_missing_tags_field(self, ad):
        row = {}
        assert not ad.has_ad_tag(row)

    def test_partial_match_in_tag_name(self, ad):
        # "KOL" is under "Advertising" parent but doesn't contain the word
        # "Advertising" — it should NOT match.
        row = {"subSessionTags": ["KOL"]}
        assert not ad.has_ad_tag(row)


# ─── AD_TAG_ID ─────────────────────────────────────────────────────

class TestAdTagId:
    def test_tag_id_is_string(self, ad):
        assert isinstance(ad.AD_TAG_ID, str)

    def test_tag_id_matches_quickcep(self, ad):
        assert ad.AD_TAG_ID == "2072600617073577986"
