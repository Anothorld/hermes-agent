"""Policy documents (Phase E) — read/write helpers for ``policy_documents``.

Three logical scopes (versioned, append-only):

* ``company_style`` — single global doc; only ``owner`` may write.
* ``user_style``    — one doc per ``owner_user_id``; user or owner may write.
* ``escalation_rules`` — single global doc; only ``owner`` may write.

Each PUT writes a new row with ``version = previous + 1`` and flips the
prior active row to ``is_active=0`` so the latest active row is queried by
``WHERE is_active=1`` ordered by ``version DESC``. History is retained.

For ``escalation_rules`` we also expose ``parse_escalation_rules`` which
extracts an ordered list of rule dicts. Markdown convention:

```
### rule_id: <id>
- signals_match: ["foo", "bar"]
- severity: high
- suggested_question: "..."
- required_facts_to_resume: ["paid_ceiling_override"]
```

Top-level overrides (``max_escalation_depth: 5``) live as a single line
``key: value`` outside any rule block.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import sqlite3
from typing import Any, Final, Literal, Optional

log = logging.getLogger(__name__)

POLICY_SCOPES: Final[tuple[str, ...]] = ("company_style", "user_style", "escalation_rules")
PolicyScope = Literal["company_style", "user_style", "escalation_rules"]


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _validate_scope(scope: str) -> None:
    if scope not in POLICY_SCOPES:
        raise ValueError(f"invalid policy scope: {scope!r}")


def _validate_owner(scope: str, owner_user_id: Optional[int]) -> None:
    if scope == "user_style":
        if owner_user_id is None:
            raise ValueError("user_style requires owner_user_id")
    else:
        if owner_user_id is not None:
            raise ValueError(f"{scope} must have owner_user_id=NULL")


def get_policy(
    conn: sqlite3.Connection,
    *,
    scope: str,
    owner_user_id: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """Return the latest active row for (scope, owner_user_id) or None."""
    _validate_scope(scope)
    _validate_owner(scope, owner_user_id)
    if owner_user_id is None:
        row = conn.execute(
            """SELECT * FROM policy_documents
                WHERE scope=? AND owner_user_id IS NULL AND is_active=1
                ORDER BY version DESC LIMIT 1""",
            (scope,),
        ).fetchone()
    else:
        row = conn.execute(
            """SELECT * FROM policy_documents
                WHERE scope=? AND owner_user_id=? AND is_active=1
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
) -> dict[str, Any]:
    """Append a new version and deactivate previous active rows.

    Caller is responsible for RBAC. Returns the new row.
    """
    _validate_scope(scope)
    _validate_owner(scope, owner_user_id)
    now = _now()
    if owner_user_id is None:
        prev = conn.execute(
            """SELECT MAX(version) AS v FROM policy_documents
                WHERE scope=? AND owner_user_id IS NULL""",
            (scope,),
        ).fetchone()
        conn.execute(
            """UPDATE policy_documents SET is_active=0
                WHERE scope=? AND owner_user_id IS NULL AND is_active=1""",
            (scope,),
        )
    else:
        prev = conn.execute(
            """SELECT MAX(version) AS v FROM policy_documents
                WHERE scope=? AND owner_user_id=?""",
            (scope, owner_user_id),
        ).fetchone()
        conn.execute(
            """UPDATE policy_documents SET is_active=0
                WHERE scope=? AND owner_user_id=? AND is_active=1""",
            (scope, owner_user_id),
        )
    next_version = ((prev["v"] if prev and prev["v"] is not None else 0) or 0) + 1
    cur = conn.execute(
        """INSERT INTO policy_documents
              (scope, owner_user_id, title, content_md, version,
               updated_by, updated_at, is_active)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
        (scope, owner_user_id, title, content_md, next_version, updated_by, now),
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
    limit: int = 50,
) -> list[dict[str, Any]]:
    _validate_scope(scope)
    _validate_owner(scope, owner_user_id)
    if owner_user_id is None:
        rows = conn.execute(
            """SELECT id, version, updated_by, updated_at, is_active
                 FROM policy_documents
                WHERE scope=? AND owner_user_id IS NULL
                ORDER BY version DESC LIMIT ?""",
            (scope, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, version, updated_by, updated_at, is_active
                 FROM policy_documents
                WHERE scope=? AND owner_user_id=?
                ORDER BY version DESC LIMIT ?""",
            (scope, owner_user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


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


__all__ = [
    "POLICY_SCOPES",
    "PolicyScope",
    "get_policy",
    "list_policy_history",
    "parse_escalation_rules",
    "put_policy",
]
