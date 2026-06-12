"""Unit tests for conversation_summary normalization."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_reply_draft(pkg_name: str = "kol_ops_bridge_pkg"):
    fq = f"{pkg_name}.reply_draft"
    if fq in sys.modules:
        return sys.modules[fq]
    spec = importlib.util.spec_from_file_location(fq, _PLUGIN_ROOT / "reply_draft.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[fq] = mod
    spec.loader.exec_module(mod)
    return mod


def test_normalize_accepts_dict_bullets():
    rd = _load_reply_draft()
    out = rd.normalize_conversation_summary({
        "bullets": ["  要点一  ", "要点二"],
    })
    assert out == {"bullets": ["要点一", "要点二"]}


def test_normalize_accepts_bare_list():
    rd = _load_reply_draft()
    out = rd.normalize_conversation_summary(["仅一条"])
    assert out == {"bullets": ["仅一条"]}


def test_normalize_clips_long_bullet():
    rd = _load_reply_draft()
    long_text = "长" * 250
    out = rd.normalize_conversation_summary({"bullets": [long_text]})
    assert out is not None
    assert len(out["bullets"][0]) <= 201
    assert out["bullets"][0].endswith("…")


def test_normalize_drops_invalid():
    rd = _load_reply_draft()
    assert rd.normalize_conversation_summary(None) is None
    assert rd.normalize_conversation_summary({"bullets": []}) is None
    assert rd.normalize_conversation_summary({"bullets": ["", "  "]}) is None
    assert rd.normalize_conversation_summary("not-a-summary") is None


def test_sanitize_reply_draft_fact_value_strips_bad_summary():
    rd = _load_reply_draft()
    out = rd.sanitize_reply_draft_fact_value({
        "decision": "pending",
        "draft": {"subject": "Re: x", "body": "hi", "to": "a@b.com"},
        "conversation_summary": {"bullets": [42, ""]},
    })
    assert "conversation_summary" not in out


def test_sanitize_reply_draft_fact_value_keeps_good_summary():
    rd = _load_reply_draft()
    out = rd.sanitize_reply_draft_fact_value({
        "decision": "pending",
        "conversation_summary": {"bullets": ["要点一"]},
    })
    assert out["conversation_summary"] == {"bullets": ["要点一"]}


def test_sanitize_hoists_summary_from_draft_subobject():
    rd = _load_reply_draft()
    out = rd.sanitize_reply_draft_fact_value({
        "decision": "pending",
        "draft": {
            "subject": "Re: x",
            "body": "hi",
            "to": "a@b.com",
            "conversation_summary": {"bullets": ["嵌套要点"]},
        },
    })
    assert out["conversation_summary"] == {"bullets": ["嵌套要点"]}
    assert "conversation_summary" not in out["draft"]
