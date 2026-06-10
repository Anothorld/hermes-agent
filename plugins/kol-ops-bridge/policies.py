"""Policy documents (Phase E) — read/write helpers for ``policy_documents``.

Three logical scopes (versioned, append-only):

* ``company_style`` — single global doc; only ``owner`` may write.
* ``user_style``    — one doc per ``owner_user_id``; user or owner may write.
* ``escalation_rules`` — single global doc; only ``owner`` may write.
* ``reply_learning`` / ``reply_strategy`` / ``pricing_calibration`` — env-scoped (TEST | LIVE).

Each PUT writes a new row with ``version = previous + 1`` and flips the
prior active row to ``is_active=0`` so the latest active row is queried by
``WHERE is_active=1`` ordered by ``version DESC``. History is retained.

For ``escalation_rules`` we also expose ``parse_escalation_rules`` which
extracts an ordered list of rule dicts. Markdown convention:

```
### rule_id: <id>
- signals_match: ["foo", "bar"]
- severity: high
- suggested_question: "KOL 报价超过 paid_ceiling，是否批准提价？"
- required_facts_to_resume: ["paid_ceiling_override"]
```

Top-level overrides (``max_escalation_depth: 5``) live as a single line
``key: value`` outside any rule block.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import re
import sqlite3
from typing import Any, Final, Literal, Optional

log = logging.getLogger(__name__)

POLICY_SCOPES: Final[tuple[str, ...]] = (
    "company_style",
    "user_style",
    "escalation_rules",
    "reply_learning",
    "reply_strategy",
    "pricing_calibration",
    "outcome_strategy",
)
ENV_SCOPED_POLICIES: Final[frozenset[str]] = frozenset({
    "reply_learning",
    "reply_strategy",
    "pricing_calibration",
    "outcome_strategy",
})
PolicyScope = Literal[
    "company_style",
    "user_style",
    "escalation_rules",
    "reply_learning",
    "reply_strategy",
    "pricing_calibration",
    "outcome_strategy",
]

# Dynamic scope family: learned discovery criteria, keyed per SPU or per
# product category — ``discovery_criteria:spu:<sku>`` /
# ``discovery_criteria:category:<slug>``. Env-scoped (TEST | LIVE), global
# owner, append-only versioning like the static scopes.
DISCOVERY_CRITERIA_SCOPE_PREFIX: Final[str] = "discovery_criteria:"
_DISCOVERY_CRITERIA_SCOPE = re.compile(
    r"^discovery_criteria:(spu|category):[A-Za-z0-9][A-Za-z0-9_\-\.]{0,79}$",
)


def is_discovery_criteria_scope(scope: str) -> bool:
    """True when ``scope`` is a valid dynamic discovery-criteria scope."""
    return bool(_DISCOVERY_CRITERIA_SCOPE.match(scope or ""))


def discovery_criteria_scope(kind: str, key: str) -> str:
    """Build a ``discovery_criteria:<kind>:<key>`` scope (validated)."""
    scope = f"{DISCOVERY_CRITERIA_SCOPE_PREFIX}{kind}:{_slugify_scope_key(key)}"
    if not is_discovery_criteria_scope(scope):
        raise ValueError(f"cannot build discovery_criteria scope from {kind!r}/{key!r}")
    return scope


def _slugify_scope_key(key: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_\-\.]+", "-", str(key or "").strip())
    cleaned = cleaned.strip("-")[:80]
    if cleaned:
        return cleaned
    # Non-ASCII keys (e.g. Chinese category labels) slug to a stable digest so
    # the scope stays deterministic for the same label.
    digest = hashlib.md5(str(key or "").encode("utf-8")).hexdigest()[:12]
    return f"x{digest}"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _validate_scope(scope: str) -> None:
    if scope not in POLICY_SCOPES and not is_discovery_criteria_scope(scope):
        raise ValueError(f"invalid policy scope: {scope!r}")


def _validate_owner(scope: str, owner_user_id: Optional[int]) -> None:
    if scope == "user_style":
        if owner_user_id is None:
            raise ValueError("user_style requires owner_user_id")
    else:
        if owner_user_id is not None:
            raise ValueError(f"{scope} must have owner_user_id=NULL")


def _resolve_env(scope: str, env: Optional[str]) -> Optional[str]:
    if scope in ENV_SCOPED_POLICIES or is_discovery_criteria_scope(scope):
        return env or "LIVE"
    return None


def get_policy(
    conn: sqlite3.Connection,
    *,
    scope: str,
    owner_user_id: Optional[int] = None,
    env: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return the latest active row for (scope, owner_user_id[, env]) or None."""
    _validate_scope(scope)
    _validate_owner(scope, owner_user_id)
    resolved_env = _resolve_env(scope, env)
    if owner_user_id is None:
        if resolved_env is None:
            row = conn.execute(
                """SELECT * FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL AND is_active=1
                      AND env IS NULL
                    ORDER BY version DESC LIMIT 1""",
                (scope,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL AND is_active=1
                      AND COALESCE(env, 'LIVE')=?
                    ORDER BY version DESC LIMIT 1""",
                (scope, resolved_env),
            ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM policy_documents
                WHERE scope=? AND owner_user_id=? AND is_active=1
                  AND env IS NULL
                ORDER BY version DESC LIMIT 1""",
            (scope, owner_user_id),
        ).fetchone()
    return dict(row) if row else None


def put_policy(
    conn: sqlite3.Connection,
    *,
    scope: str,
    content_md: str,
    updated_by: str,
    owner_user_id: Optional[int] = None,
    title: Optional[str] = None,
    env: Optional[str] = None,
) -> dict[str, Any]:
    """Append a new version and deactivate previous active rows.

    Caller is responsible for RBAC. Returns the new row.
    """
    _validate_scope(scope)
    _validate_owner(scope, owner_user_id)
    resolved_env = _resolve_env(scope, env)
    now = _now()
    if owner_user_id is None:
        if resolved_env is None:
            prev = conn.execute(
                """SELECT MAX(version) AS v FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL AND env IS NULL""",
                (scope,),
            ).fetchone()
            conn.execute(
                """UPDATE policy_documents SET is_active=0
                    WHERE scope=? AND owner_user_id IS NULL AND is_active=1
                      AND env IS NULL""",
                (scope,),
            )
        else:
            prev = conn.execute(
                """SELECT MAX(version) AS v FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL
                      AND COALESCE(env, 'LIVE')=?""",
                (scope, resolved_env),
            ).fetchone()
            conn.execute(
                """UPDATE policy_documents SET is_active=0
                    WHERE scope=? AND owner_user_id IS NULL AND is_active=1
                      AND COALESCE(env, 'LIVE')=?""",
                (scope, resolved_env),
            )
    else:
        prev = conn.execute(
            """SELECT MAX(version) AS v FROM policy_documents
                WHERE scope=? AND owner_user_id=? AND env IS NULL""",
            (scope, owner_user_id),
        ).fetchone()
        conn.execute(
            """UPDATE policy_documents SET is_active=0
                WHERE scope=? AND owner_user_id=? AND is_active=1 AND env IS NULL""",
            (scope, owner_user_id),
        )
    next_version = ((prev["v"] if prev and prev["v"] is not None else 0) or 0) + 1
    cur = conn.execute(
        """INSERT INTO policy_documents
              (scope, owner_user_id, title, content_md, version,
               updated_by, updated_at, is_active, env)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
        (
            scope, owner_user_id, title, content_md, next_version,
            updated_by, now, resolved_env,
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    row = conn.execute(
        "SELECT * FROM policy_documents WHERE id=?", (new_id,)
    ).fetchone()
    return dict(row)


def list_policy_history(
    conn: sqlite3.Connection,
    *,
    scope: str,
    owner_user_id: Optional[int] = None,
    env: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    _validate_scope(scope)
    _validate_owner(scope, owner_user_id)
    resolved_env = _resolve_env(scope, env)
    if owner_user_id is None:
        if resolved_env is None:
            rows = conn.execute(
                """SELECT id, version, updated_by, updated_at, is_active, env
                     FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL AND env IS NULL
                    ORDER BY version DESC LIMIT ?""",
                (scope, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, version, updated_by, updated_at, is_active, env
                     FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL
                      AND COALESCE(env, 'LIVE')=?
                    ORDER BY version DESC LIMIT ?""",
                (scope, resolved_env, limit),
            ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, version, updated_by, updated_at, is_active, env
                 FROM policy_documents
                WHERE scope=? AND owner_user_id=? AND env IS NULL
                ORDER BY version DESC LIMIT ?""",
            (scope, owner_user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_policy_version(
    conn: sqlite3.Connection,
    *,
    scope: str,
    version: int,
    owner_user_id: Optional[int] = None,
    env: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Return a specific historical version row (active or not) or None."""
    _validate_scope(scope)
    _validate_owner(scope, owner_user_id)
    resolved_env = _resolve_env(scope, env)
    if owner_user_id is None:
        if resolved_env is None:
            row = conn.execute(
                """SELECT * FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL AND env IS NULL
                      AND version=? LIMIT 1""",
                (scope, version),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM policy_documents
                    WHERE scope=? AND owner_user_id IS NULL
                      AND COALESCE(env, 'LIVE')=? AND version=? LIMIT 1""",
                (scope, resolved_env, version),
            ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM policy_documents
                WHERE scope=? AND owner_user_id=? AND env IS NULL
                  AND version=? LIMIT 1""",
            (scope, owner_user_id, version),
        ).fetchone()
    return dict(row) if row else None


def rollback_policy(
    conn: sqlite3.Connection,
    *,
    scope: str,
    to_version: int,
    updated_by: str,
    owner_user_id: Optional[int] = None,
    env: Optional[str] = None,
) -> dict[str, Any]:
    """Roll a policy back to a prior version's content.

    Implemented as a forward write (new active version carrying the old
    content), so the version chain and audit trail are preserved — no history
    is destroyed. Returns the new active row.

    Raises ValueError when ``to_version`` does not exist for the scope.
    """
    target = get_policy_version(
        conn, scope=scope, version=to_version, owner_user_id=owner_user_id, env=env,
    )
    if target is None:
        raise ValueError(
            f"policy {scope!r} has no version {to_version} to roll back to",
        )
    title = target.get("title") or scope
    return put_policy(
        conn,
        scope=scope,
        content_md=target.get("content_md") or "",
        updated_by=updated_by,
        owner_user_id=owner_user_id,
        title=f"{title} (rollback→v{to_version})",
        env=env,
    )


# ---------------------------------------------------------------------------
# escalation_rules markdown parser
# ---------------------------------------------------------------------------

_RULE_HEADER = re.compile(r"^###\s+rule_id\s*:\s*(?P<id>[A-Za-z0-9_\-]+)\s*$")
_TOP_KV = re.compile(r"^([a-z_][a-z0-9_]*)\s*:\s*(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(?P<key>[a-z_][a-z0-9_]*)\s*:\s*(?P<val>.+?)\s*$")


def _coerce(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        # naive list parse: split on commas, strip quotes
        inner = raw[1:-1].strip()
        if not inner:
            return []
        items = [p.strip().strip('"').strip("'") for p in inner.split(",")]
        return [it for it in items if it]
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    if raw.isdigit():
        return int(raw)
    try:
        return float(raw) if "." in raw else raw
    except ValueError:
        return raw
    finally:
        pass


def parse_escalation_rules(content_md: str) -> dict[str, Any]:
    """Parse the markdown body of the ``escalation_rules`` policy.

    Returns ``{"top": {...}, "rules": [ {id, signals_match, ...}, ... ]}``.
    Unknown/malformed lines are ignored, never raise. Used by the
    classifier rule-match step (no LLM cost per dispatch).
    """
    top: dict[str, Any] = {}
    rules: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    in_rule_block = False
    for raw_line in content_md.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = _RULE_HEADER.match(line)
        if m:
            if current is not None:
                rules.append(current)
            current = {"id": m.group("id")}
            in_rule_block = True
            continue
        if in_rule_block and current is not None:
            mb = _BULLET.match(line)
            if mb:
                current[mb.group("key")] = _coerce(mb.group("val"))
                continue
            # exit rule block on next ### or non-bullet header
            if line.startswith("#"):
                in_rule_block = False
                rules.append(current)
                current = None
                continue
        if not in_rule_block:
            mt = _TOP_KV.match(line)
            if mt and not line.startswith("#"):
                top[mt.group(1)] = _coerce(mt.group(2))
    if current is not None:
        rules.append(current)
    return {"top": top, "rules": rules}


DEFAULT_MAX_ESCALATION_DEPTH: Final[int] = 3


def match_escalation_rules(
    parsed: dict[str, Any],
    signals: Any,
) -> dict[str, Any]:
    """Deterministically match classifier signals against escalation rules.

    A rule matches when **all** of its ``signals_match`` names are present in
    the detected signals (subset semantics). Rules are evaluated in document
    order; the first match wins. This replaces the per-dispatch LLM rule-match
    step so the ``escalation_hint`` is reproducible.

    Args:
        parsed: output of :func:`parse_escalation_rules`
            (``{"top": {...}, "rules": [...]}``).
        signals: list of signal names (``["not_received", ...]``) or list of
            signal dicts (``[{"name": "not_received", ...}]``).

    Returns:
        An ``escalation_hint`` dict: ``{should_consider, matched_rule_id,
        reason, suggested_question, required_facts_to_resume, severity,
        max_escalation_depth}``.
    """
    top = parsed.get("top") or {}
    rules = parsed.get("rules") or []
    names = _signal_names(signals)
    max_depth = top.get("max_escalation_depth", DEFAULT_MAX_ESCALATION_DEPTH)
    empty = {
        "should_consider": False,
        "matched_rule_id": "",
        "reason": "",
        "suggested_question": "",
        "required_facts_to_resume": [],
        "severity": "normal",
        "max_escalation_depth": max_depth,
    }
    if not names or not rules:
        return empty
    for rule in rules:
        wanted = rule.get("signals_match") or []
        if isinstance(wanted, str):
            wanted = [wanted]
        if wanted and set(wanted).issubset(names):
            required = rule.get("required_facts_to_resume") or []
            if isinstance(required, str):
                required = [required]
            return {
                "should_consider": True,
                "matched_rule_id": rule.get("id", ""),
                "reason": "rule pattern matched",
                "suggested_question": rule.get("suggested_question", ""),
                "required_facts_to_resume": list(required),
                "severity": rule.get("severity", "normal"),
                "max_escalation_depth": max_depth,
            }
    return empty


def _signal_names(signals: Any) -> set[str]:
    names: set[str] = set()
    for sig in signals or []:
        if isinstance(sig, str):
            names.add(sig)
        elif isinstance(sig, dict) and sig.get("name"):
            names.add(str(sig["name"]))
    return names


__all__ = [
    "DISCOVERY_CRITERIA_SCOPE_PREFIX",
    "ENV_SCOPED_POLICIES",
    "POLICY_SCOPES",
    "PolicyScope",
    "DEFAULT_MAX_ESCALATION_DEPTH",
    "discovery_criteria_scope",
    "is_discovery_criteria_scope",
    "get_policy",
    "get_policy_version",
    "list_policy_history",
    "match_escalation_rules",
    "parse_escalation_rules",
    "put_policy",
    "rollback_policy",
]
