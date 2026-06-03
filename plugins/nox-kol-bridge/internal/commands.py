"""Subcommand implementations for ``nox_kol_tool.py``."""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping, Optional

from schemas import DEFAULT_MONTHLY_BUDGET, GATES_REQUIRING_AUDIT
from internal import cli_runner, nox_cache, quota_ledger, summarize
from internal.audit_hooks import AuditContext, emit_nox_audit
from internal.campaign_gate import (
    NoxCampaignGateError,
    assert_live_allowed,
    diligence_dimensions,
    resolve_cache_timezone,
    resolve_campaign_id,
    resolve_monthly_budget,
    resolve_supplement_max_calls,
)
from internal.creator_args import build_creator_read_args
from internal.cli_runner import NoxCliError, NoxInsufficientCreditError
from internal.nox_auth import auth_status, classify_auth_error
from internal.supplement_ledger import (
    SupplementQuotaExceededError,
    assert_supplement_allowed,
    commit_supplement,
    supplement_snapshot,
)
from internal.quota_remote import remote_used_credits
from internal.locks import file_lock
from internal.normalize import (
    cache_key_contacts,
    cache_key_diligence,
    cache_key_monitor,
    cache_key_search,
)
from internal.nox_cache import current_cache_month, lookup, put_alias, resolve_alias, store
from internal.quota_ledger import QuotaExceededError, commit, release, reserve, snapshot


def _validate_gate(gate: str) -> None:
    if gate not in GATES_REQUIRING_AUDIT and gate != "":
        raise ValueError(f"unknown gate: {gate}")


def cmd_doctor(*, env: str) -> dict[str, Any]:
    """Preflight: CLI on PATH, stored auth, env key hydration."""
    status = auth_status(env=env)
    if env.upper() == "LIVE" and status.get("cli_on_path") and status.get("env_api_key"):
        from internal.nox_auth import bootstrap_auth_from_env, has_stored_api_key

        if not has_stored_api_key():
            try:
                status["bootstrap_attempted"] = True
                status["bootstrap_ok"] = bootstrap_auth_from_env()
                status["stored_api_key"] = has_stored_api_key()
                status["ok"] = bool(status["bootstrap_ok"])
                if status["bootstrap_ok"]:
                    status["detail"] = "auto-configured from NOXINFLUENCER_API_KEY"
            except Exception as exc:  # noqa: BLE001
                status["bootstrap_attempted"] = True
                status["bootstrap_ok"] = False
                status["detail"] = str(exc)
    return status


def cmd_quota_snapshot(
    *,
    env: str,
    monthly_budget: int,
    tz_name: str,
    refresh_remote: bool = False,
) -> dict[str, Any]:
    month = current_cache_month(tz_name)
    local = snapshot(monthly_budget=monthly_budget, cache_month=month)
    preflight = auth_status(env=env)
    try:
        remote = cli_runner.quota(env, lang="en", use_cache=not refresh_remote)
    except NoxCliError as exc:
        msg = str(exc)
        remote = {"success": False, "error": msg}
        code = classify_auth_error(msg)
        if code:
            remote["error_code"] = code
    except Exception as exc:  # noqa: BLE001 — NoxAuthError from ensure_nox_auth
        from internal.nox_auth import NoxAuthError

        if isinstance(exc, NoxAuthError):
            remote = {
                "success": False,
                "error": str(exc),
                "error_code": "NOX_AUTH_MISSING",
            }
        else:
            raise
    reconcile: dict[str, Any] | None = None
    used = remote_used_credits(remote) if isinstance(remote, dict) else None
    if used is not None and env.upper() == "LIVE":
        from internal.quota_ledger import reconcile_committed_floor

        reconcile = reconcile_committed_floor(
            used, monthly_budget=monthly_budget, cache_month=month
        )
        local = snapshot(monthly_budget=monthly_budget, cache_month=month)
    stats = nox_cache.cache_stats(month, tz_name=tz_name)
    out: dict[str, Any] = {
        "local": local,
        "remote_quota": remote,
        "cache_stats": stats,
        "reconcile": reconcile,
        "auth_preflight": preflight,
    }
    if isinstance(remote, dict) and remote.get("error_code") == "NOX_AUTH_MISSING":
        out["error_code"] = "NOX_AUTH_MISSING"
    return out


def cmd_diligence_pack(
    *,
    env: str,
    gate: str,
    monthly_budget: int,
    tz_name: str,
    lang: str,
    nox_creator_id: Optional[str],
    platform: Optional[str],
    url: Optional[str],
    channel_id: Optional[str],
    dimensions: list[str],
    include_cooperation: bool,
    campaign_config: Optional[Mapping[str, Any]] = None,
    audit: Optional[AuditContext] = None,
) -> dict[str, Any]:
    _validate_gate(gate)
    if gate != "shortlist_confirm":
        raise ValueError("diligence-pack requires gate=shortlist_confirm")

    cfg = dict(campaign_config or {})
    assert_live_allowed(env, cfg, operation="diligence_pack", gate=gate)
    tz_name = resolve_cache_timezone(cfg, tz_name)
    monthly_budget = resolve_monthly_budget(cfg, monthly_budget)
    dims = diligence_dimensions(cfg, list(dimensions) or ["profile", "audience", "content"])
    if include_cooperation and "cooperation" not in dims:
        dims.append("cooperation")

    month = current_cache_month(tz_name)
    creator_id = nox_creator_id
    if not creator_id and platform and (url or channel_id):
        creator_id = resolve_alias(platform, url or channel_id or "")
    if not creator_id:
        estimated = _bundle_calls(dims)
        lock_key = f"diligence_url_{platform or 'x'}_{url or channel_id or 'unknown'}"
        with file_lock(lock_key):
            try:
                reserve(estimated, monthly_budget=monthly_budget, cache_month=month)
                bundle, creator_id = _fetch_diligence_bundle(
                    env=env,
                    lang=lang,
                    monthly_budget=monthly_budget,
                    month=month,
                    dims=dims,
                    nox_creator_id=None,
                    platform=platform,
                    url=url,
                    channel_id=channel_id,
                    from_cache=False,
                )
                commit(estimated, estimated, monthly_budget=monthly_budget, cache_month=month)
            except NoxInsufficientCreditError:
                release(estimated, cache_month=month)
                raise
            except Exception:
                release(estimated, cache_month=month)
                raise
        if not creator_id:
            raise ValueError("could not resolve nox_creator_id from url/channel")
        if platform and (url or channel_id):
            put_alias(platform, url or channel_id or "", creator_id)
        ck = cache_key_diligence(creator_id, dims, lang)
        store(month, ck, "diligence_pack", bundle)
        summary = summarize.summarize_diligence_pack(bundle)
        return emit_nox_audit(
            _envelope(False, month, ck, bundle, summary, api_calls=estimated),
            audit,
        )

    ck = cache_key_diligence(creator_id, dims, lang)
    hit = lookup(month, ck, tz_name=tz_name)
    if hit:
        summary = summarize.summarize_diligence_pack(hit["response"])
        return emit_nox_audit(
            _envelope(True, month, ck, hit["response"], summary, api_calls=0),
            audit,
        )

    estimated = _bundle_calls(dims)
    with file_lock(f"diligence_{creator_id}"):
        hit2 = lookup(month, ck, tz_name=tz_name)
        if hit2:
            summary = summarize.summarize_diligence_pack(hit2["response"])
            return emit_nox_audit(
                _envelope(True, month, ck, hit2["response"], summary, api_calls=0),
                audit,
            )
        try:
            reserve(estimated, monthly_budget=monthly_budget, cache_month=month)
            bundle, resolved_id = _fetch_diligence_bundle(
                env=env,
                lang=lang,
                monthly_budget=monthly_budget,
                month=month,
                dims=dims,
                nox_creator_id=creator_id,
                platform=platform,
                url=url,
                channel_id=channel_id,
                from_cache=False,
            )
            commit(_bundle_calls(dims), estimated, monthly_budget=monthly_budget, cache_month=month)
        except NoxInsufficientCreditError:
            release(estimated, cache_month=month)
            raise
        except Exception:
            release(estimated, cache_month=month)
            raise
    store(month, ck, "diligence_pack", bundle)
    if platform and (url or channel_id):
        put_alias(platform, url or channel_id or "", resolved_id or creator_id)
    summary = summarize.summarize_diligence_pack(bundle)
    return emit_nox_audit(
        _envelope(False, month, ck, bundle, summary, api_calls=estimated),
        audit,
    )


def _bundle_calls(dims: list[str]) -> int:
    return len(dims)


def _fetch_diligence_bundle(
    *,
    env: str,
    lang: str,
    monthly_budget: int,
    month: str,
    dims: list[str],
    nox_creator_id: Optional[str],
    platform: Optional[str],
    url: Optional[str],
    channel_id: Optional[str],
    from_cache: bool,
) -> tuple[dict[str, Any], Optional[str]]:
    bundle: dict[str, Any] = {}
    resolved_id = nox_creator_id
    for dim in dims:
        env_resp = _creator_read(
            env, lang, dim, resolved_id, platform, url, channel_id
        )
        bundle[dim] = env_resp
        data = env_resp.get("data") if isinstance(env_resp, dict) else {}
        if isinstance(data, dict) and data.get("creator_id"):
            resolved_id = data["creator_id"]
    return bundle, resolved_id


def _creator_read(
    env: str,
    lang: str,
    dimension: str,
    creator_id: Optional[str],
    platform: Optional[str],
    url: Optional[str],
    channel_id: Optional[str],
) -> dict[str, Any]:
    if env.upper() == "TEST":
        fix = cli_runner.load_fixture("diligence_pack.json")
        return fix.get(dimension) or fix.get("profile", fix)

    need_detail = dimension in ("audience", "content", "cooperation")
    frag = build_creator_read_args(
        dimension,
        creator_id=creator_id,
        url=url,
        platform=platform,
        channel_id=channel_id,
        detail=need_detail,
    )
    return cli_runner.run_cli(["creator", *frag], env_mode=env, lang=lang)


def cmd_contacts(
    *,
    env: str,
    gate: str,
    monthly_budget: int,
    tz_name: str,
    lang: str,
    nox_creator_id: str,
    platform: Optional[str],
    url: Optional[str],
    campaign_config: Optional[Mapping[str, Any]] = None,
    audit: Optional[AuditContext] = None,
) -> dict[str, Any]:
    _validate_gate(gate)
    if gate != "pre_outreach_confirm":
        raise ValueError("contacts requires gate=pre_outreach_confirm")

    cfg = dict(campaign_config or {})
    assert_live_allowed(env, cfg, operation="contacts", gate=gate)
    tz_name = resolve_cache_timezone(cfg, tz_name)
    monthly_budget = resolve_monthly_budget(cfg, monthly_budget)

    month = current_cache_month(tz_name)
    creator_id = nox_creator_id or ""
    if not creator_id and platform and url:
        creator_id = resolve_alias(platform, url) or ""
    if not creator_id:
        raise ValueError("nox_creator_id or platform+url required")

    ck = cache_key_contacts(creator_id, lang)
    hit = lookup(month, ck, tz_name=tz_name)
    if hit:
        summary = summarize.summarize_contacts(hit["response"])
        out = _envelope(True, month, ck, hit["response"], summary, api_calls=0)
        out["normalized_summary"]["identity.nox_contacts_cached_month"] = month
        return emit_nox_audit(out, audit)

    estimated = 1
    with file_lock(f"contacts_{creator_id}"):
        hit2 = lookup(month, ck, tz_name=tz_name)
        if hit2:
            summary = summarize.summarize_contacts(hit2["response"])
            out = _envelope(True, month, ck, hit2["response"], summary, api_calls=0)
            out["normalized_summary"]["identity.nox_contacts_cached_month"] = month
            return emit_nox_audit(out, audit)
        try:
            reserve(estimated, monthly_budget=monthly_budget, cache_month=month)
            if env.upper() == "TEST":
                resp = cli_runner.load_fixture("contacts.json")
            else:
                args = ["creator", "contacts", creator_id]
                resp = cli_runner.run_cli(args, env_mode=env, lang=lang)
            commit(estimated, estimated, monthly_budget=monthly_budget, cache_month=month)
        except NoxInsufficientCreditError:
            release(estimated, cache_month=month)
            raise
        except Exception:
            release(estimated, cache_month=month)
            raise
    store(month, ck, "contacts", resp)
    summary = summarize.summarize_contacts(resp)
    out = _envelope(False, month, ck, resp, summary, api_calls=estimated)
    out["normalized_summary"]["identity.nox_contacts_cached_month"] = month
    return emit_nox_audit(out, audit)


def cmd_creator_search(
    *,
    env: str,
    gate: str,
    monthly_budget: int,
    tz_name: str,
    lang: str,
    platform: str,
    body: dict[str, Any],
    page_num: int,
    campaign_config: Optional[Mapping[str, Any]] = None,
    audit: Optional[AuditContext] = None,
) -> dict[str, Any]:
    _validate_gate(gate)
    if gate != "supplement_search":
        raise ValueError("creator-search requires gate=supplement_search")

    cfg = dict(campaign_config or {})
    assert_live_allowed(env, cfg, operation="creator_search", gate=gate)
    tz_name = resolve_cache_timezone(cfg, tz_name)
    monthly_budget = resolve_monthly_budget(cfg, monthly_budget)
    extra_platforms = body.get("platforms")
    if isinstance(extra_platforms, list) and len(extra_platforms) > 2:
        raise ValueError("creator-search body.platforms supports at most 2 entries")
    campaign_id = resolve_campaign_id(cfg) or ""
    if env.upper() == "LIVE" and not campaign_id:
        raise NoxCampaignGateError(
            "campaign_id must be present in campaign config file for supplement search"
        )
    supplement_max = resolve_supplement_max_calls(cfg)

    month = current_cache_month(tz_name)
    ck = cache_key_search(platform, body, page_num)
    hit = lookup(month, ck, tz_name=tz_name)
    if hit:
        out = {
            "cache_hit": True,
            "cache_month": month,
            "cache_key": ck,
            "api_calls": 0,
            "response": hit["response"],
            "supplement_usage": supplement_snapshot(campaign_id, max_calls=supplement_max)
            if campaign_id
            else None,
        }
        return emit_nox_audit(out, audit)

    if campaign_id:
        assert_supplement_allowed(
            campaign_id, max_calls=supplement_max, cache_month=month
        )

    estimated = 1
    try:
        reserve(estimated, monthly_budget=monthly_budget, cache_month=month)
        if env.upper() == "TEST":
            resp = cli_runner.load_fixture("creator_search.json")
        else:
            search_body = dict(body)
            search_body.setdefault("page_num", page_num)
            resp = cli_runner.run_creator_search(
                platform,
                search_body,
                env_mode=env,
                lang=lang,
            )
        commit(estimated, estimated, monthly_budget=monthly_budget, cache_month=month)
        if campaign_id:
            commit_supplement(campaign_id, estimated, cache_month=month)
    except NoxInsufficientCreditError:
        release(estimated, cache_month=month)
        raise
    except Exception:
        release(estimated, cache_month=month)
        raise
    store(month, ck, "creator_search", resp)
    out = {
        "cache_hit": False,
        "cache_month": month,
        "cache_key": ck,
        "api_calls": estimated,
        "response": resp,
        "supplement_usage": supplement_snapshot(campaign_id, max_calls=supplement_max)
        if campaign_id
        else None,
    }
    return emit_nox_audit(out, audit)


def cmd_monitor_setup(
    *,
    env: str,
    gate: str,
    monthly_budget: int,
    tz_name: str,
    lang: str,
    video_url: str,
    project_id: Optional[str],
    force: bool,
    campaign_config: Optional[Mapping[str, Any]] = None,
    audit: Optional[AuditContext] = None,
) -> dict[str, Any]:
    _validate_gate(gate)
    if gate != "post_publish_confirm":
        raise ValueError("monitor-setup requires gate=post_publish_confirm")

    cfg = dict(campaign_config or {})
    assert_live_allowed(env, cfg, operation="monitor_setup", gate=gate)
    tz_name = resolve_cache_timezone(cfg, tz_name)
    monthly_budget = resolve_monthly_budget(cfg, monthly_budget)

    month = current_cache_month(tz_name)
    ck = cache_key_monitor(video_url, project_id)
    hit = lookup(month, ck, tz_name=tz_name)
    if hit:
        out = {
            "cache_hit": True,
            "cache_month": month,
            "cache_key": ck,
            "api_calls": 0,
            "response": hit["response"],
        }
        return emit_nox_audit(out, audit)

    if not force:
        out = {
            "cache_hit": False,
            "dry_run": True,
            "cache_month": month,
            "cache_key": ck,
            "api_calls": 0,
            "message": "Re-run with --force after operator approval",
            "video_url": video_url,
        }
        return emit_nox_audit(out, audit)

    estimated = 2
    try:
        reserve(estimated, monthly_budget=monthly_budget, cache_month=month)
        if env.upper() == "TEST":
            resp = cli_runner.load_fixture("monitor_setup.json")
        else:
            existing_pid: Optional[int] = None
            if project_id:
                try:
                    existing_pid = int(project_id)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"invalid --project-id: {project_id!r}") from exc
            if existing_pid is None:
                slug = uuid.uuid4().hex[:8]
                create = cli_runner.run_monitor_create(
                    f"kol-{month}-{slug}",
                    env_mode=env,
                    lang=lang,
                )
                existing_pid = cli_runner.extract_project_id(create)
            else:
                create = {"skipped": True, "project_id": existing_pid}
            add = cli_runner.run_monitor_add_task(
                project_id=existing_pid,
                video_url=video_url,
                env_mode=env,
                lang=lang,
            )
            resp = {"create": create, "add_task": add, "project_id": existing_pid}
        commit(estimated, estimated, monthly_budget=monthly_budget, cache_month=month)
    except NoxInsufficientCreditError:
        release(estimated, cache_month=month)
        raise
    except Exception:
        release(estimated, cache_month=month)
        raise
    store(month, ck, "monitor_setup", resp)
    out = {
        "cache_hit": False,
        "cache_month": month,
        "cache_key": ck,
        "api_calls": estimated,
        "response": resp,
    }
    return emit_nox_audit(out, audit)


def cmd_cache_stats(
    *,
    tz_name: str,
    campaign_id: Optional[str] = None,
    supplement_max_calls: int = 30,
) -> dict[str, Any]:
    month = current_cache_month(tz_name)
    out: dict[str, Any] = {
        "cache": nox_cache.cache_stats(month, tz_name=tz_name),
        "usage": snapshot(cache_month=month),
    }
    if campaign_id:
        out["supplement_usage"] = supplement_snapshot(
            campaign_id, max_calls=supplement_max_calls
        )
    return out


def _envelope(
    cache_hit: bool,
    month: str,
    ck: str,
    response: dict,
    summary: dict,
    *,
    api_calls: int,
) -> dict[str, Any]:
    return {
        "cache_hit": cache_hit,
        "cache_month": month,
        "cache_key": ck,
        "api_calls": api_calls,
        "response": response,
        "normalized_summary": summary,
        "facts_hint": {
            "identity.nox_cache_month": month,
            "identity.nox_cache_key": ck,
        },
    }
