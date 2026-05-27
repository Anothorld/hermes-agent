"""Console-side helpers for normalising ``campaign_id`` at the HTTP
boundary so a frontend bug (most commonly
``encodeURIComponent(null) → "null"``) cannot persist a sentinel string
into CAL.

Mirrors the same defense landed on the bridge side at
``plugins/kol-ops-bridge/plugin_api.py``: the bridge is the last line of
defense, but applying the same rule on the console gives the operator a
clearer 400 with a frontend-routable error code instead of a downstream
"campaign_config is missing" message.

Exports:

* :data:`NULL_SENTINEL_CAMPAIGN_IDS` — the canonical set of sentinel
  strings we treat as "caller meant no campaign_id".
* :func:`norm_campaign_id` — pure normaliser. Returns ``None`` for the
  sentinels, the empty string, or whitespace; returns the stripped
  string otherwise.
* :class:`CampaignIdNormaliserMixin` — Pydantic v2 mixin. Any subclass
  declaring a ``campaign_id`` field gets the coercion applied in
  ``mode="before"``. Required-vs-optional semantics are governed by
  the subclass's own field annotation (a sentinel string coerced to
  ``None`` against a required field surfaces as a clean 422).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, field_validator

NULL_SENTINEL_CAMPAIGN_IDS: frozenset[str] = frozenset({
    "null", "undefined", "nan", "none",
})


def norm_campaign_id(value: Any) -> Optional[str]:
    """Coerce sentinel strings and empty/whitespace to ``None``.

    Leave anything else as the stripped string. Always returns ``None``
    for input ``None`` so callers don't have to special-case it.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.lower() in NULL_SENTINEL_CAMPAIGN_IDS:
        return None
    return s


class CampaignIdNormaliserMixin(BaseModel):
    """Pydantic mixin that auto-normalises ``campaign_id`` on subclasses.

    Uses ``check_fields=False`` so the validator is harmless on subclass
    models that don't declare ``campaign_id``. Pydantic v2 wires it up
    only where the field actually exists.
    """

    @field_validator("campaign_id", mode="before", check_fields=False)
    @classmethod
    def _coerce_null_string_campaign_id(cls, v: Any) -> Optional[str]:
        return norm_campaign_id(v)
