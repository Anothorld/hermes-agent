"""Classifier golden-set runner — deterministic sanitize + optional LLM layer."""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from . import classifier_facts as cf

# Synthetic inbound bodies for Layer-2 eval (see playground/classifier_eval/LLM_EVAL.md).
_CASE_LATEST_EMAIL: dict[str, dict[str, str]] = {
    "interest_inquiry_downgrade": {
        "body": "Thanks! What deliverables and formats do you need from me?",
    },
    "interest_positive_kept": {
        "body": "Yes, I'm interested — happy to move forward with your proposal.",
    },
    "deliverables_rewrite_on_budget": {
        "body": "What's your budget for this? I usually do 2 IG reels.",
    },
    "deliverables_kept_on_accept": {
        "body": "Sounds good, I accept the terms as discussed.",
    },
    "agreed_terms_dropped": {
        "body": "My rate is $1200 for one dedicated post.",
    },
    "sku_locked_dropped_oos": {
        "body": "Can we feature SKU TS-9999 instead? That's the one I prefer.",
    },
    "interest_negative_downgrade": {
        "body": "Thanks but I'm going to pass on this collaboration.",
    },
    "asks_timeline_inquiry": {
        "body": "When would you need the content live?",
    },
    "asks_budget_inquiry": {
        "body": "What budget range did you have in mind?",
    },
    "asks_deliverables_inquiry": {
        "body": "What platforms and post types are you looking for?",
    },
    "triple_inquiry_downgrade": {
        "body": "What deliverables, timeline, and budget are you targeting?",
    },
}

_CLASSIFIER_JSON_CONTRACT = """\
Return ONE JSON object only (no markdown). Minimum shape:
{
  "active_goals_by_lane": {"commerce": null, "fulfillment": null, "publish": null, "meta": null},
  "facts_extracted": {"identity": {}, "offer": {}, "fulfillment": {}, "payout": {}, "approval": {}},
  "signals": [{"name": "<signal>", "confidence": 0.0, "evidence": "<quote>"}],
  "ambiguity": "",
  "escalation_hint": {"should_consider": false, "reason": "", "matched_rule_id": "",
    "suggested_question": "", "required_facts_to_resume": []}
}
Use dotted fact keys inside each namespace (e.g. offer.interest_signal).
"""


def _resolve_cases_dir() -> Optional[Path]:
    """Find ``playground/classifier_eval/cases`` or bundled ``eval/cases``."""
    plugin_root = Path(__file__).resolve().parent
    candidates = (
        plugin_root / "eval" / "cases",
        plugin_root.parents[2] / "playground" / "classifier_eval" / "cases",
        plugin_root.parents[3] / "playground" / "classifier_eval" / "cases",
    )
    for cand in candidates:
        if cand.is_dir() and any(cand.glob("*.json")):
            return cand
    return None


def load_cases(cases_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    root = cases_dir or _resolve_cases_dir()
    if root is None:
        return []
    cases: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            cases.extend(data)
        elif isinstance(data, dict):
            if "cases" in data:
                cases.extend(data["cases"])
            else:
                cases.append(data)
    return cases


def run_deterministic_eval(
    cases: Optional[list[dict[str, Any]]] = None,
    *,
    cases_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Run sanitize expectations; return pass/fail/skip counts + failures."""
    rows = cases if cases is not None else load_cases(cases_dir)
    passed = failed = skipped = 0
    failures: list[dict[str, str]] = []
    for case in rows:
        cid = str(case.get("id") or "?")
        exp = case.get("expect_sanitize") or {}
        if not exp:
            skipped += 1
            continue
        namespaces = case.get("input_namespaces") or {}
        signals = case.get("input_signals") or []
        out_ns, adjustments = cf.sanitize_classifier_namespaces(namespaces, signals)
        ok = True
        reason = ""
        if exp.get("offer"):
            for key, val in exp["offer"].items():
                got = out_ns.get("offer", {}).get(key)
                if got != val:
                    ok = False
                    reason = f"offer.{key} expected {val!r} got {got!r}"
                    break
            if ok:
                for absent in exp.get("offer_absent") or []:
                    if absent in out_ns.get("offer", {}):
                        ok = False
                        reason = f"expected absent {absent}"
                        break
        if ok and exp.get("adjustments_min") is not None:
            if len(adjustments) < int(exp["adjustments_min"]):
                ok = False
                reason = f"expected >= {exp['adjustments_min']} adjustments"
        if ok:
            passed += 1
        else:
            failed += 1
            failures.append({"id": cid, "reason": reason})
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": len(rows),
        "failures": failures[:20],
    }


def _derive_expect_llm(case: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Build ``expect_llm`` from explicit field or ``expect_sanitize``."""
    explicit = case.get("expect_llm")
    if explicit:
        return explicit
    san = case.get("expect_sanitize") or {}
    if not san:
        return None
    out: dict[str, Any] = {}
    if san.get("offer"):
        out.setdefault("facts_extracted", {})["offer"] = dict(san["offer"])
    absent = san.get("offer_absent") or []
    if absent:
        out["offer_absent"] = list(absent)
    sigs = case.get("input_signals") or []
    names = [s.get("name") for s in sigs if isinstance(s, dict) and s.get("name")]
    if names:
        out["signals_include"] = names
    return out or None


def _latest_email_for_case(case: dict[str, Any]) -> Optional[dict[str, str]]:
    if isinstance(case.get("input_latest_email"), dict):
        return case["input_latest_email"]
    cid = str(case.get("id") or "")
    if cid in _CASE_LATEST_EMAIL:
        return _CASE_LATEST_EMAIL[cid]
    signals = case.get("input_signals") or []
    names = [
        str(s.get("name"))
        for s in signals
        if isinstance(s, dict) and s.get("name")
    ]
    if names:
        return {"body": f"[eval] KOL message triggering signals: {', '.join(names)}"}
    return {"body": "[eval] KOL reply (synthetic golden-set fixture)."}


def build_classifier_prompt(case: dict[str, Any]) -> Optional[str]:
    """Assemble a kol-email-stage-classifier eval prompt for one case."""
    email = _latest_email_for_case(case)
    if not email:
        return None
    body = email.get("body") or ""
    thread = case.get("input_thread_history") or []
    goal_state = case.get("input_goal_state") or {}
    payload = {
        "latest_email": {
            "from": email.get("from", "kol@example.com"),
            "subject": email.get("subject", "Re: collaboration"),
            "date": email.get("date", "Mon, 1 Jun 2026 12:00:00 +0000"),
            "body": body,
        },
        "thread_history": thread,
        "current_goal_state": goal_state,
        "anomaly_signals": case.get("input_anomaly_signals") or {},
    }
    return (
        "You are kol-email-stage-classifier in an offline golden-set eval.\n"
        f"{_CLASSIFIER_JSON_CONTRACT}\n"
        f"CASE_ID={case.get('id')}\n"
        f"INPUT_JSON:\n{json.dumps(payload, indent=2)}"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("classifier output must be a JSON object")
    return parsed


def compare_expect_llm(
    case: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[bool, str]:
    """Compare classifier JSON to ``expect_llm`` (or derived) expectations."""
    exp = _derive_expect_llm(case)
    if not exp:
        return True, ""
    facts = parsed.get("facts_extracted") or {}
    if exp.get("facts_extracted"):
        for ns, expected in exp["facts_extracted"].items():
            if not isinstance(expected, dict):
                continue
            got_ns = facts.get(ns) if isinstance(facts.get(ns), dict) else {}
            for key, val in expected.items():
                got = got_ns.get(key)
                if got != val:
                    return False, f"{ns}.{key} expected {val!r} got {got!r}"
    for absent in exp.get("offer_absent") or []:
        offer = facts.get("offer") if isinstance(facts.get("offer"), dict) else {}
        if absent in offer:
            return False, f"expected absent {absent}"
    sig_names = {
        s.get("name")
        for s in (parsed.get("signals") or [])
        if isinstance(s, dict) and s.get("name")
    }
    for required in exp.get("signals_include") or []:
        if required not in sig_names:
            return False, f"missing signal {required!r}"
    for forbidden in exp.get("signals_exclude") or []:
        if forbidden in sig_names:
            return False, f"unexpected signal {forbidden!r}"
    return True, ""


def invoke_llm_via_openai_compatible(prompt: str) -> str:
    """Call an OpenAI-compatible chat API (env-driven)."""
    base = (
        os.environ.get("KOL_CLASSIFIER_EVAL_API_URL")
        or os.environ.get("OPENAI_API_BASE")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    api_key = os.environ.get("KOL_CLASSIFIER_EVAL_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "set KOL_CLASSIFIER_EVAL_API_KEY or OPENAI_API_KEY for --llm eval",
        )
    model = os.environ.get("KOL_CLASSIFIER_EVAL_MODEL", "gpt-4.1-mini")
    url = f"{base}/chat/completions"
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": "Output JSON only."},
            {"role": "user", "content": prompt},
        ],
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("LLM response missing choices")
    content = (choices[0].get("message") or {}).get("content") or ""
    if not str(content).strip():
        raise RuntimeError("LLM response empty content")
    return str(content)


def invoke_llm_via_command(prompt: str) -> str:
    """Run ``KOL_CLASSIFIER_EVAL_CMD`` — stdin prompt, stdout JSON."""
    cmd = os.environ.get("KOL_CLASSIFIER_EVAL_CMD", "").strip()
    if not cmd:
        raise RuntimeError("KOL_CLASSIFIER_EVAL_CMD not set")
    proc = subprocess.run(
        cmd,
        shell=True,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=int(os.environ.get("KOL_CLASSIFIER_EVAL_CMD_TIMEOUT", "180")),
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"KOL_CLASSIFIER_EVAL_CMD exit {proc.returncode}: {proc.stderr[:500]}",
        )
    return proc.stdout


def invoke_classifier_llm(
    prompt: str,
    *,
    runner: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """Invoke external classifier LLM and parse JSON output."""
    if runner is not None:
        raw = runner(prompt)
    elif os.environ.get("KOL_CLASSIFIER_EVAL_CMD", "").strip():
        raw = invoke_llm_via_command(prompt)
    else:
        raw = invoke_llm_via_openai_compatible(prompt)
    return _extract_json_object(raw)


def run_llm_eval(
    cases: Optional[list[dict[str, Any]]] = None,
    *,
    cases_dir: Optional[Path] = None,
    runner: Optional[Callable[[str], str]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Layer-2 eval: call classifier LLM per case with ``expect_llm`` checks."""
    rows = cases if cases is not None else load_cases(cases_dir)
    passed = failed = skipped = 0
    failures: list[dict[str, str]] = []
    for case in rows:
        cid = str(case.get("id") or "?")
        exp = _derive_expect_llm(case)
        if not exp:
            skipped += 1
            continue
        prompt = build_classifier_prompt(case)
        if not prompt:
            skipped += 1
            continue
        if dry_run:
            skipped += 1
            continue
        try:
            parsed = invoke_classifier_llm(prompt, runner=runner)
            ok, reason = compare_expect_llm(case, parsed)
        except Exception as exc:  # noqa: BLE001
            ok, reason = False, str(exc)
        if ok:
            passed += 1
        else:
            failed += 1
            failures.append({"id": cid, "reason": reason})
    return {
        "layer": "llm",
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": len(rows),
        "failures": failures[:20],
    }
