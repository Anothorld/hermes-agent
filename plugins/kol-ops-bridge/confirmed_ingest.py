"""Deterministic ingest of agent-confirmed KOL candidates into CAL.

Single entry point for the ``ingest-confirmed-candidate`` Bridge endpoint.
Orchestrates identity upsert → identity facts → candidate upsert in a
fixed order without LLM involvement.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Final, Mapping, Optional

from . import cal  # type: ignore[import-not-found]

# Keys allowed in ``identity_facts`` payloads (base + provenance triples).
_DISCOVERY_BASE_KEYS: Final[tuple[str, ...]] = (
    "identity.content_pillars",
    "identity.signature_hooks",
    "identity.voice_descriptors",
    "identity.hero_post_url",
    "identity.hero_post_note",
    "identity.recommendation_reason",
    "identity.instagram_profile_url",
    "identity.tiktok_profile_url",
    "identity.youtube_profile_url",
    "identity.facebook_profile_url",
    "identity.twitter_profile_url",
    "identity.threads_profile_url",
    "identity.linktree_url",
    "identity.personal_site_url",
    "identity.email_source",
    "identity.email_discovered_at",
    "identity.email_discovered_url",
    "identity.email_discovery_tier",
)

_PROVENANCE_BASES: Final[tuple[str, ...]] = tuple(
    k for k in _DISCOVERY_BASE_KEYS
    if not k.startswith("identity.email_")
)

ALLOWED_IDENTITY_FACT_KEYS: Final[frozenset[str]] = frozenset(
    list(_DISCOVERY_BASE_KEYS)
    + [f"{base}_source" for base in _PROVENANCE_BASES]
    + [f"{base}_discovered_at" for base in _PROVENANCE_BASES]
    + [f"{base}_discovered_url" for base in _PROVENANCE_BASES]
)


class IngestValidationError(ValueError):
    """Client-side payload validation failure."""


def _ingest_state_path() -> Path:
    root = Path(
        os.environ.get(
            "HERMES_KOL_OPS_INGEST_STATE_DIR",
            os.path.expanduser("~/.hermes/kol-ops-bridge"),
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root / "processed_ingests.json"


def _load_processed_ingests() -> dict[str, Any]:
    path = _ingest_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_processed_ingests(data: dict[str, Any]) -> None:
    path = _ingest_state_path()
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _ingest_dedup_key(*, env: str, campaign_id: str, ingest_id: str) -> str:
    return f"{env}:{campaign_id}:{ingest_id}"


def validate_identity_facts(facts: Mapping[str, Any]) -> None:
    unknown = [k for k in facts if k not in ALLOWED_IDENTITY_FACT_KEYS]
    if unknown:
        raise IngestValidationError(
            f"identity_facts contains disallowed keys: {', '.join(sorted(unknown))}"
        )


def _filter_non_overwriting_facts(
    *,
    identity_id: int,
    facts: Mapping[str, Any],
    env: str,
) -> tuple[dict[str, Any], list[str]]:
    """Return facts to write and keys skipped because a value already exists."""
    existing = cal.latest_facts_for(identity_id=identity_id, campaign_id=None, env=env)
    to_write: dict[str, Any] = {}
    skipped: list[str] = []
    for key, value in facts.items():
        cur = existing.get(key)
        if cur is not None and cur != "" and cur != []:
            skipped.append(key)
            continue
        to_write[key] = value
    return to_write, skipped


def ingest_confirmed_candidate(
    *,
    campaign_id: str,
    env: str,
    source: str,
    identity: Mapping[str, Any],
    candidate: Mapping[str, Any],
    identity_facts: Optional[Mapping[str, Any]] = None,
    ingest_id: Optional[str] = None,
) -> dict[str, Any]:
    """Persist one confirmed candidate through identity → facts → candidate.

    Returns a structured summary with ``written`` / ``skipped`` sections.
    When ``ingest_id`` is supplied and was already processed successfully,
    returns the cached outcome with ``already_imported: true``.
    """
    if env not in ("TEST", "LIVE"):
        raise IngestValidationError(f"env must be TEST or LIVE; got {env!r}")
    if not campaign_id:
        raise IngestValidationError("campaign_id required")
    if not source:
        raise IngestValidationError("source required")

    primary_handle = identity.get("primary_handle")
    if not isinstance(primary_handle, str) or not primary_handle.strip():
        raise IngestValidationError("identity.primary_handle required")
    primary_handle = primary_handle.strip().lstrip("@")
    platform = identity.get("platform") or "instagram"
    if not isinstance(platform, str) or not platform.strip():
        raise IngestValidationError("identity.platform must be a non-empty string")

    cand_source = candidate.get("source")
    if not isinstance(cand_source, str) or not cand_source.strip():
        raise IngestValidationError("candidate.source required")

    facts_in = dict(identity_facts or {})
    if facts_in:
        validate_identity_facts(facts_in)

    if ingest_id:
        dedup_key = _ingest_dedup_key(env=env, campaign_id=campaign_id, ingest_id=ingest_id)
        processed = _load_processed_ingests()
        cached = processed.get(dedup_key)
        if isinstance(cached, dict):
            out = dict(cached)
            out["already_imported"] = True
            return out

    display_name = identity.get("display_name")
    primary_email = identity.get("primary_email")

    identity_id = cal.upsert_identity(
        primary_handle=primary_handle,
        platform=platform,
        primary_email=primary_email if primary_email else None,
        display_name=display_name if display_name else None,
        env=env,
    )
    if identity_id is None:
        raise RuntimeError("upsert_identity failed")

    written_facts: list[str] = []
    skipped_facts: list[str] = []
    if facts_in:
        to_write, skipped_facts = _filter_non_overwriting_facts(
            identity_id=identity_id, facts=facts_in, env=env,
        )
        if to_write:
            try:
                cal.write_facts_multi(
                    identity_id=identity_id,
                    campaign_id=None,
                    namespaces={"identity": to_write},
                    source=source,
                    env=env,
                )
            except cal.FactNamespaceError as exc:
                raise IngestValidationError(str(exc)) from exc
            written_facts = sorted(to_write.keys())

    candidate_id = cal.upsert_candidate(
        campaign_id=campaign_id,
        identity_id=identity_id,
        source=cand_source,
        discovery_score=candidate.get("discovery_score"),
        candidate_status=candidate.get("candidate_status") or "discovered",
        payload=candidate.get("payload"),
        env=env,
    )
    if candidate_id is None:
        raise RuntimeError("upsert_candidate failed")

    result: dict[str, Any] = {
        "campaign_id": campaign_id,
        "env": env,
        "identity_id": identity_id,
        "candidate_id": candidate_id,
        "ingest_id": ingest_id,
        "written": {
            "identity": True,
            "facts": written_facts,
            "candidate": True,
        },
        "skipped": {
            "facts": skipped_facts,
        },
        "already_imported": False,
    }

    if ingest_id:
        dedup_key = _ingest_dedup_key(env=env, campaign_id=campaign_id, ingest_id=ingest_id)
        processed = _load_processed_ingests()
        processed[dedup_key] = result
        _save_processed_ingests(processed)

    return result
