"""Global KOL outreach touch history (cross-campaign).

Used to enforce a discovery cooldown (no new candidates within 14 days of
the last confirmed outreach send) and to surface prior-touch tags in the
console shortlist and KOL detail views.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Final, Iterable, Mapping, Optional

OUTREACH_COOLDOWN_DAYS: Final[int] = 14
_OUTREACH_SENT_EVENT: Final[str] = "outreach.sent"
_OUTREACH_SENT_AT_KEY: Final[str] = "offer.outreach_sent_at"
_OUTREACH_SENT_KEY: Final[str] = "offer.outreach_sent"


class OutreachCooldownActive(ValueError):
    """Raised when ``add-candidate`` is blocked by the 14-day cooldown."""

    def __init__(
        self,
        *,
        identity_id: int,
        last_touch_at: str,
        last_touch_campaign_id: str | None,
        cooldown_days: int = OUTREACH_COOLDOWN_DAYS,
    ) -> None:
        self.identity_id = identity_id
        self.last_touch_at = last_touch_at
        self.last_touch_campaign_id = last_touch_campaign_id
        self.cooldown_days = cooldown_days
        super().__init__(
            f"identity {identity_id} was outreached at {last_touch_at}; "
            f"within {cooldown_days}-day discovery cooldown"
        )


def _parse_iso_ts(raw: Any) -> Optional[_dt.datetime]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(raw), tz=_dt.timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            parsed = _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.astimezone(_dt.timezone.utc)
    return None


def _fact_is_true(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1"):
            return True
        if s in ("false", "0", ""):
            return False
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        decoded = value
    return decoded is True


def _decode_fact_ts(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        decoded = value
    if isinstance(decoded, str):
        return decoded.strip() or None
    return None


def _merge_touch(
    acc: dict[int, dict[str, Any]],
    identity_id: int,
    ts_raw: Any,
    campaign_id: str | None,
) -> None:
    parsed = _parse_iso_ts(ts_raw)
    if parsed is None:
        return
    iso = parsed.isoformat(timespec="seconds")
    row = acc.get(identity_id)
    if row is None or iso > row["last_touch_at"]:
        acc[identity_id] = {
            "identity_id": identity_id,
            "last_touch_at": iso,
            "last_touch_campaign_id": campaign_id,
        }


def within_outreach_cooldown(last_touch_at: str | None, *, now: _dt.datetime | None = None) -> bool:
    """True when ``last_touch_at`` is within ``OUTREACH_COOLDOWN_DAYS``."""
    parsed = _parse_iso_ts(last_touch_at)
    if parsed is None:
        return False
    ref = now or _dt.datetime.now(_dt.timezone.utc)
    delta = ref - parsed
    return delta < _dt.timedelta(days=OUTREACH_COOLDOWN_DAYS)


def enrich_touch_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Add ``within_cooldown`` and ``has_prior_touch`` to a touch row."""
    last = row.get("last_touch_at")
    out = dict(row)
    out["within_cooldown"] = within_outreach_cooldown(
        last if isinstance(last, str) else None,
    )
    out["has_prior_touch"] = bool(last)
    out["cooldown_days"] = OUTREACH_COOLDOWN_DAYS
    return out


def batch_global_outreach_touch(
    conn: Any,
    identity_ids: Iterable[int],
    *,
    env: str = "LIVE",
) -> dict[int, dict[str, Any]]:
    """Latest outreach send per identity across all campaigns (same env)."""
    ids = sorted({int(i) for i in identity_ids if i is not None})
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    acc: dict[int, dict[str, Any]] = {}

    for r in conn.execute(
        f"""SELECT identity_id, campaign_id, ts
              FROM kol_conversation_events
             WHERE env=? AND event_type=?
               AND identity_id IN ({placeholders})""",
        (env, _OUTREACH_SENT_EVENT, *ids),
    ):
        _merge_touch(acc, int(r["identity_id"]), r["ts"], r["campaign_id"])

    for r in conn.execute(
        f"""SELECT identity_id, campaign_id, fact_value
              FROM kol_facts_latest
             WHERE env=? AND fact_key=?
               AND identity_id IN ({placeholders})""",
        (env, _OUTREACH_SENT_AT_KEY, *ids),
    ):
        ts = _decode_fact_ts(r["fact_value"])
        if ts:
            _merge_touch(acc, int(r["identity_id"]), ts, r["campaign_id"])

    for r in conn.execute(
        f"""SELECT identity_id, campaign_id, captured_at, fact_value
              FROM kol_facts_latest
             WHERE env=? AND fact_key=?
               AND identity_id IN ({placeholders})""",
        (env, _OUTREACH_SENT_KEY, *ids),
    ):
        if _fact_is_true(r["fact_value"]):
            _merge_touch(
                acc, int(r["identity_id"]), r["captured_at"], r["campaign_id"],
            )

    return {iid: enrich_touch_row(row) for iid, row in acc.items()}


def merge_touch_rows(
    primary: Mapping[str, Any] | None,
    secondary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Pick the later of two touch rows (for API + per-campaign fact merge)."""
    rows = [r for r in (primary, secondary) if isinstance(r, Mapping) and r.get("last_touch_at")]
    if not rows:
        return None
    best = max(rows, key=lambda r: str(r["last_touch_at"]))
    return enrich_touch_row(best)


def resolve_identity_ids_by_handles(
    conn: Any,
    handles: Iterable[str],
    *,
    env: str = "LIVE",
) -> dict[str, int]:
    """Map normalized handle -> identity_id (first match per handle)."""
    norms: list[str] = []
    seen: set[str] = set()
    for h in handles:
        if not isinstance(h, str):
            continue
        norm = h.strip().lstrip("@").lower()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        norms.append(norm)
    if not norms:
        return {}
    placeholders = ",".join("?" * len(norms))
    rows = conn.execute(
        f"""SELECT id, primary_handle FROM kol_identity
             WHERE env=? AND LOWER(TRIM(primary_handle)) IN ({placeholders})""",
        (env, *norms),
    ).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        handle = (r["primary_handle"] or "").strip().lstrip("@").lower()
        if handle and handle not in out:
            out[handle] = int(r["id"])
    return out


def list_cooldown_handles(
    conn: Any,
    *,
    env: str = "LIVE",
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Handles with an outreach touch inside the discovery cooldown window."""
    limit = max(1, min(int(limit), 10_000))
    now = _dt.datetime.now(_dt.timezone.utc)
    cutoff = (now - _dt.timedelta(days=OUTREACH_COOLDOWN_DAYS)).isoformat(timespec="seconds")

    touch_by_id: dict[int, dict[str, Any]] = {}
    for r in conn.execute(
        """SELECT identity_id, campaign_id, ts
             FROM kol_conversation_events
            WHERE env=? AND event_type=? AND ts >= ?""",
        (env, _OUTREACH_SENT_EVENT, cutoff),
    ):
        _merge_touch(touch_by_id, int(r["identity_id"]), r["ts"], r["campaign_id"])

    for r in conn.execute(
        """SELECT identity_id, campaign_id, fact_value
             FROM kol_facts_latest
           WHERE env=? AND fact_key=?""",
        (env, _OUTREACH_SENT_AT_KEY),
    ):
        ts = _decode_fact_ts(r["fact_value"])
        if ts and ts >= cutoff:
            _merge_touch(touch_by_id, int(r["identity_id"]), ts, r["campaign_id"])

    for r in conn.execute(
        """SELECT identity_id, campaign_id, captured_at, fact_value
             FROM kol_facts_latest
           WHERE env=? AND fact_key=?""",
        (env, _OUTREACH_SENT_KEY),
    ):
        if _fact_is_true(r["fact_value"]):
            cap = r["captured_at"]
            if cap and cap >= cutoff:
                _merge_touch(touch_by_id, int(r["identity_id"]), cap, r["campaign_id"])

    if not touch_by_id:
        return []

    ids = sorted(touch_by_id)
    id_ph = ",".join("?" * len(ids))
    handle_rows = conn.execute(
        f"""SELECT id, primary_handle FROM kol_identity
             WHERE env=? AND id IN ({id_ph})""",
        (env, *ids),
    ).fetchall()
    handle_by_id = {int(r["id"]): r["primary_handle"] for r in handle_rows}

    items: list[dict[str, Any]] = []
    for iid, touch in touch_by_id.items():
        enriched = enrich_touch_row(touch)
        if not enriched.get("within_cooldown"):
            continue
        handle = handle_by_id.get(iid)
        if not handle:
            continue
        items.append({
            "identity_id": iid,
            "handle": str(handle).strip().lstrip("@"),
            **enriched,
        })
        if len(items) >= limit:
            break
    return items
