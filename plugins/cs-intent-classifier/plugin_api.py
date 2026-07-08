"""FastAPI router for cs-intent-classifier.

Endpoints (mounted at root in serve.py, or under /api/plugins/cs-intent-classifier
when loaded in-process by the Hermes plugin manager):

- GET  /health
- POST /classify                      → classify an inbound email
- GET  /gate-extract/{session_id}     → latest gate_extract for a session (bridge seam)
- GET  /intent/{session_id}           → predicted + corrected (Console)
- PATCH /intent/{session_id}          → operator correction (Console)
- GET  /learning/intent-metrics       → observability aggregates (Phase 4)
- GET  /learning/intent-trend         → pass-rate time series (Phase 4)
- GET  /learning/distill-log          → distill decision audit (Phase 4)
- GET  /config/intent-scope           → in_scope whitelist (intent_scope.yaml)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from . import db
from .classifier import FabricationError, classify as run_classify
from .schemas import (
    ClassifyRequest,
    ClassifyResponse,
    CorrectionRequest,
    GateExtract,
    IntentReadResponse,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "cs-intent-classifier"}


@router.get("/metrics/trend")
def metrics_trend_route(
    env: str = Query("LIVE"),
    since: str = Query(..., description="ISO lower bound (inclusive), UTC"),
    until: str = Query(..., description="ISO upper bound (exclusive), UTC"),
) -> dict[str, Any]:
    """Per-day 意图分类错误率 series for the Console 数据页签 (read-only).

    Beijing natural day buckets (UTC+8). Returns 分子/分母/错误率 per day.
    See docs/features/metrics/GUIDE.md for the audit-locked formula.
    """
    return db.metrics_trend(env=env, since=since, until=until)


@router.get("/config/intent-scope")
def get_intent_scope_config() -> dict[str, Any]:
    """Return the in_scope whitelist used for gate + operator UI close-bar logic."""
    from .classifier import _load_scope

    scope = _load_scope()
    return {
        "scope": scope,
        "in_scope_intents": [k for k, v in scope.items() if v],
        "out_of_scope_intents": [k for k, v in scope.items() if not v],
    }


@router.post("/classify", response_model=ClassifyResponse)
def classify_endpoint(req: ClassifyRequest) -> ClassifyResponse:
    """Classify an inbound email. Returns the full gate_extract.

    On fabrication-guard failure returns HTTP 422 (no fake data).
    """
    try:
        gate_extract = run_classify(
            subject=req.subject,
            body=req.body,
            metadata=req.metadata or {},
            conversation_history=req.conversation_history or [],
        )
    except FabricationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"fabrication_guard failed: {exc}",
        )
    # Validate against schema
    ge = GateExtract.model_validate(gate_extract)
    payload = ge.model_dump()
    ts = db.insert_classification(
        session_id=req.session_id,
        env=req.env,
        gate_extract=payload,
        model_version=ge.model_version,
        classifier_source=ge.classifier_source,
    )
    return ClassifyResponse(
        session_id=req.session_id,
        env=req.env,
        classified_at=ts,
        gate_extract=ge,
    )


@router.get("/gate-extract/{session_id}")
def get_gate_extract(
    session_id: str,
    env: str = Query("LIVE"),
) -> dict[str, Any]:
    """Latest gate_extract for a session. 404 if never classified."""
    row = db.latest_classification(session_id=session_id, env=env)
    if not row:
        raise HTTPException(status_code=404, detail="not classified")
    return row["gate_extract"]


@router.get("/intent/{session_id}", response_model=IntentReadResponse)
def get_intent(
    session_id: str,
    env: str = Query("LIVE"),
) -> IntentReadResponse:
    """Read predicted + corrected intent for Console."""
    row = db.latest_classification(session_id=session_id, env=env)
    predicted = row["gate_extract"] if row else None
    corrections = db.corrections_for_session(session_id=session_id, env=env)
    corrected = corrections[0]["corrected"] if corrections else None
    return IntentReadResponse(
        session_id=session_id,
        env=env,
        predicted=GateExtract.model_validate(predicted) if predicted else None,
        corrected=GateExtract.model_validate(corrected) if corrected else None,
        corrections=corrections,
    )


class IntentBatchRequest(BaseModel):
    session_ids: list[str] = []
    env: str = "LIVE"


@router.post("/intents/batch")
def batch_intent_codes(body: IntentBatchRequest) -> dict[str, Any]:
    """Latest classifier intent codes for many sessions (Console list enrichment)."""
    if body.env not in ("LIVE", "TEST"):
        raise HTTPException(status_code=400, detail="env must be LIVE or TEST")
    ids = body.session_ids[:200]
    return {
        "env": body.env,
        "intents_by_session": db.latest_intent_codes_batch(session_ids=ids, env=body.env),
    }


@router.patch("/intent/{session_id}", response_model=IntentReadResponse)
def patch_intent(
    session_id: str,
    body: CorrectionRequest,
) -> IntentReadResponse:
    """Record an operator correction. Does NOT auto-relaunch (per confirmed decision).

    Allows correction even when no AI prediction exists (e.g. the session was
    launched before CS_INTENT_ENABLED, or the classifier was unreachable at
    inbound time). In that case the correction becomes an operator ground-truth
    label — predicted is stored as {} and the corrected gate_extract is built
    from the operator's chosen primary_intent.
    """
    if body.env != "LIVE" and body.env != "TEST":
        raise HTTPException(status_code=400, detail="env must be LIVE or TEST")
    row = db.latest_classification(session_id=session_id, env=body.env)
    if row:
        predicted = row["gate_extract"]
        corrected = _apply_correction(predicted, body)
    else:
        # No AI prediction — record an operator ground-truth label.
        predicted = {}
        corrected = _build_label_only_correction(body)
    db.insert_correction(
        session_id=session_id,
        env=body.env,
        predicted=predicted,
        corrected=corrected,
        reason=body.reason,
        operator_id=body.operator_id,
        subject=body.subject,
    )
    # Re-read to return fresh state
    return get_intent(session_id, env=body.env)


def _build_label_only_correction(body: CorrectionRequest) -> dict[str, Any]:
    """Build a minimal corrected gate_extract when no AI prediction existed.

    The operator's chosen primary_intent becomes the label; in_scope is derived
    from the intent scope config so the label carries the same scope semantics
    as a real classification.
    """
    from .classifier import _load_scope, _current_model_version

    primary = body.primary_intent or "spam_irrelevant"
    scope = _load_scope()
    in_scope = bool(scope.get(primary, False))
    return {
        "intents": [
            {
                "intent": primary,
                "in_scope": in_scope,
                "confidence": "high",
                "related_orders": [],
                "related_products": [],
                "post_sale_signal": None,
                "urgency": "medium",
                "snippet": "",
            }
        ],
        "primary_intent": primary,
        "in_scope": in_scope,
        "route": "auto_handle" if in_scope else "escalate",
        "urgency": "medium",
        "emotion": {"value": "neutral", "confidence": "low"},
        "language": {"value": "en", "confidence": 0.5},
        "products": [],
        "orders": [],
        "customer_region": {"country": None, "province_state": None, "source": "unknown", "confidence": "low"},
        "customer_segment": "unknown",
        "summary_zh": "",
        "hindsight_keywords": [],
        "conversation_stage": "unknown",
        "response_template_hint": None,
        "attachment_hint": False,
        "pii_flag": False,
        "ambiguous": False,
        "needs_clarification": None,
        "threat_signal": None,
        "model_version": _current_model_version(),
        "classifier_source": "operator_label",
        "uncertain_fields": [],
        "null_fields": ["customer_region"],
        "fabrication_guard": True,
    }


def _apply_correction(predicted: dict[str, Any], body: CorrectionRequest) -> dict[str, Any]:
    """Apply operator overrides to a predicted gate_extract, returning corrected copy."""
    out = dict(predicted)
    if body.primary_intent:
        out["primary_intent"] = body.primary_intent
        # recompute in_scope based on overrides or primary
    intent_overrides = body.intent_overrides or []
    if intent_overrides:
        intents = [dict(i) for i in (out.get("intents") or [])]
        for ov in intent_overrides:
            # find matching intent by name, or append
            target = next((i for i in intents if i.get("intent") == ov.intent), None)
            if target is None:
                target = {"intent": ov.intent, "in_scope": False, "confidence": "high", "related_orders": [], "related_products": [], "post_sale_signal": None, "urgency": "medium", "snippet": ""}
                intents.append(target)
            if ov.in_scope is not None:
                target["in_scope"] = ov.in_scope
        out["intents"] = intents
        out["in_scope"] = any(i.get("in_scope") for i in intents)
    return out


# ── Learning / observability endpoints (Phase 4 stubs, wired to db) ──


class MetricsResponse(BaseModel):
    env: str
    since: str
    until: str
    total_classifications: int
    total_corrections: int
    correction_rate: float
    by_intent: dict[str, int]
    by_source: dict[str, int]


@router.get("/learning/intent-metrics", response_model=MetricsResponse)
def intent_metrics(
    env: str = Query("LIVE"),
    since: str = Query(""),
    until: str = Query(""),
) -> MetricsResponse:
    """Aggregate metrics for the Console effect panel."""
    until = until or _utcnow()
    # corrections list
    corrections = db.list_corrections(env=env, since=since, until=until, limit=10000)
    # classifications: approximate count via a direct query
    with db.connect() as conn:
        count_row = conn.execute(
            "SELECT COUNT(*) AS c FROM cs_intent_classifications WHERE env=?",
            (env,),
        ).fetchone()
        total = int(count_row["c"]) if count_row else 0
    by_intent: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for c in corrections:
        pred = c.get("predicted") or {}
        pi = pred.get("primary_intent") or "unknown"
        by_intent[pi] = by_intent.get(pi, 0) + 1
        src = pred.get("classifier_source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
    rate = (len(corrections) / total) if total else 0.0
    return MetricsResponse(
        env=env,
        since=since or "epoch",
        until=until,
        total_classifications=total,
        total_corrections=len(corrections),
        correction_rate=rate,
        by_intent=by_intent,
        by_source=by_source,
    )


@router.get("/learning/intent-trend")
def intent_trend(
    env: str = Query("LIVE"),
    days: int = Query(14, ge=1, le=90),
) -> dict[str, Any]:
    rows = db.eval_trend(env=env, days=days)
    return {"env": env, "days": days, "snapshots": rows}


@router.get("/learning/distill-log")
def distill_log(
    env: str = Query("LIVE"),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    runs = db.list_job_runs(env=env, job="intent_optimize_distill", limit=limit)
    return {"env": env, "runs": runs}
