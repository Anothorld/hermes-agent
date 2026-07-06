"""End-to-end smoke test: classify ~50 real CAL emails with the configured LLM.

Pulls subject + preview + intention_tags from the cs-ops-bridge CAL, runs each
through cs_intent_classifier_pkg.classifier.classify(), and prints a comparison
table vs QuickCEP's intention_tags (rough ground truth).

Run::
    cd plugins/cs-intent-classifier
    python3 scripts/run_cal_smoke.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

# Load the plugin as a synthetic package (mirrors serve.py / tests/conftest.py).
import importlib.util
from types import ModuleType

_PKG_NAME = "cs_intent_classifier_pkg"


def _load_pkg() -> ModuleType:
    if _PKG_NAME in sys.modules:
        return sys.modules[_PKG_NAME]
    pkg = ModuleType(_PKG_NAME)
    pkg.__path__ = [str(_PLUGIN_ROOT)]  # type: ignore[attr-defined]
    sys.modules[_PKG_NAME] = pkg
    for sub in ("schemas", "db", "classifier", "intent_provider", "plugin_api", "learning", "eval_runner"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG_NAME}.{sub}",
            _PLUGIN_ROOT / f"{sub}.py",
            submodule_search_locations=[str(_PLUGIN_ROOT)],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = _PKG_NAME
        sys.modules[f"{_PKG_NAME}.{sub}"] = mod
        assert spec.loader
        spec.loader.exec_module(mod)
        setattr(pkg, sub, mod)
    return pkg


_load_pkg()

# Load .env
_env_path = _PLUGIN_ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from cs_intent_classifier_pkg import classifier  # type: ignore[attr-defined]

CAL_DB = os.environ.get("CS_OPS_CAL_DB", "/Users/arnold/.hermes/cs-ops-bridge/cal.db")
TAG_MAP = {"产品咨询": "product_inquiry", "物流咨询": "logistics_inquiry"}


def fetch_cal_emails(limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(CAL_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT quickcep_session_id, customer_email, email_subject,
                  last_message_preview, intention_tags, status
           FROM cs_session
           WHERE email_subject IS NOT NULL AND email_subject != ''
           ORDER BY updated_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        tags = []
        try:
            tags = json.loads(r["intention_tags"]) if r["intention_tags"] else []
        except Exception:
            tags = []
        out.append({
            "session_id": r["quickcep_session_id"],
            "customer_email": r["customer_email"] or "",
            "subject": r["email_subject"] or "",
            "body": r["last_message_preview"] or "",
            "qc_tags": tags,
            "status": r["status"],
        })
    return out


def main() -> None:
    emails = fetch_cal_emails(limit=50)
    print(f"Loaded {len(emails)} emails from CAL {CAL_DB}")
    print(f"LLM model={os.environ.get('CS_INTENT_LLM_MODEL')} base={os.environ.get('CS_INTENT_LLM_BASE_URL')}")
    print("=" * 100)

    matches = 0
    keyword_count = 0
    llm_count = 0
    errors = 0
    rows_out = []
    t0 = time.monotonic()
    for i, e in enumerate(emails, 1):
        subject = e["subject"]
        # Use subject + preview as body (full body would need get-messages per session).
        body = e["body"] or subject
        metadata = {
            "customer_email": e["customer_email"],
            "intention_tags": e["qc_tags"],
            "has_prior_session": False,
        }
        try:
            ge = classifier.classify(subject=subject, body=body, metadata=metadata)
        except Exception as exc:
            print(f"[{i:02d}] ERROR session={e['session_id']}: {exc}")
            errors += 1
            continue
        primary = ge.get("primary_intent", "?")
        source = ge.get("classifier_source", "?")
        in_scope = ge.get("in_scope")
        qc_primary = next((TAG_MAP[t] for t in e["qc_tags"] if t in TAG_MAP), None)
        # "match" = our primary intent agrees with one of QuickCEP's tags (rough)
        matched = qc_primary is not None and primary == qc_primary
        if matched:
            matches += 1
        if source == "keyword":
            keyword_count += 1
        else:
            llm_count += 1
        uncertain = ge.get("uncertain_fields") or []
        region = (ge.get("customer_region") or {}).get("country") or "—"
        emoji = "✓" if matched else ("~" if qc_primary is None else "✗")
        print(
            f"[{i:02d}] {emoji} {source:7s} {primary:20s} scope={str(in_scope):5s} "
            f"qc={qc_primary or '—':18s} region={region:6s} "
            f"uncertain={len(uncertain)} | {subject[:50]}"
        )
        rows_out.append({
            "session_id": e["session_id"],
            "subject": subject,
            "predicted": primary,
            "in_scope": in_scope,
            "classifier_source": source,
            "qc_primary": qc_primary,
            "matched": matched,
            "region": region,
            "emotion": (ge.get("emotion") or {}).get("value"),
            "language": (ge.get("language") or {}).get("value"),
            "urgency": ge.get("urgency"),
        })

    elapsed = time.monotonic() - t0
    total = len(emails) - errors
    print("=" * 100)
    print(f"Total: {total}  matched(qc): {matches}  keyword: {keyword_count}  llm: {llm_count}  errors: {errors}")
    print(f"Agreement with QuickCEP tag (rough): {matches}/{total} = {matches/max(total,1):.1%}")
    print(f"Elapsed: {elapsed:.1f}s  avg {elapsed/max(total,1):.2f}s/email")

    # Save full results
    out_path = _PLUGIN_ROOT / "data" / "cal_smoke_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Full results → {out_path}")


if __name__ == "__main__":
    main()
