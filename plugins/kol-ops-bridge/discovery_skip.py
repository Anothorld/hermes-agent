"""Discovery exclusion rules for KOL handles with prior relationship outcomes.

Operators flag archived KOLs with outcomes that must never re-enter the
discovery pool. The instagram-kol-discovery skill pre-filters via
``list-discovery-skip-handles``; ``add-candidate`` and
``ingest-confirmed-candidate`` enforce the same rules server-side.
"""

from __future__ import annotations

from typing import Any, Final, Optional

try:
    from . import cal  # type: ignore[import-not-found]
except ImportError:
    import cal  # type: ignore[no-redef]

# Outcomes that hard-block discovery (console labels in kolOutcomes.ts).
DISCOVERY_SKIP_OUTCOMES: Final[frozenset[str]] = frozenset({
    "competitor",      # 竞品 — 不合作
    "success",         # 已合作完成 / 达成合作
    "aborted",         # 主动叫停
    "legacy_collab",   # 历史合作
})


class DiscoverySkipActive(ValueError):
    """Raised when discovery ingest is blocked by a prior archived outcome."""

    def __init__(
        self,
        *,
        identity_id: int,
        handle: str | None,
        reason: str,
    ) -> None:
        self.identity_id = identity_id
        self.handle = handle
        self.reason = reason
        super().__init__(
            f"identity {identity_id} ({handle or '?'}) blocked from discovery: {reason}"
        )


def is_discovery_skip_outcome(last_outcome: Any) -> bool:
    """True when ``last_outcome`` is in the operator skip set."""
    if not last_outcome or not isinstance(last_outcome, str):
        return False
    return last_outcome.strip() in DISCOVERY_SKIP_OUTCOMES


def resolve_discovery_skip_reason(
    *,
    identity_id: int,
    env: str,
) -> str | None:
    """Return skip reason code, or None when discovery is allowed."""
    rel = cal.get_relationship(identity_id) or {}
    last_outcome = rel.get("last_outcome")
    if is_discovery_skip_outcome(last_outcome):
        return str(last_outcome)
    try:
        from . import legacy_outcome_repair as _lor

        if _lor.should_skip_misclassified_legacy(identity_id=identity_id):
            return _lor.REPAIR_TARGET_OUTCOME
    except Exception:
        pass
    return None


def assert_discovery_not_skipped(*, identity_id: int, env: str) -> None:
    """Raise ``DiscoverySkipActive`` when the identity must not be discovered."""
    reason = resolve_discovery_skip_reason(identity_id=identity_id, env=env)
    if reason is None:
        return
    ident = cal.get_identity(identity_id) or {}
    raise DiscoverySkipActive(
        identity_id=identity_id,
        handle=ident.get("primary_handle"),
        reason=reason,
    )


def normalize_skip_handle(handle: Any) -> str | None:
    """Lowercase handle for exclusion-set membership checks."""
    if handle is None:
        return None
    text = str(handle).strip().lstrip("@").lower()
    return text or None
