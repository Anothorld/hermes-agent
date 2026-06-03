#!/usr/bin/env python3
"""Deterministic CLI for NoxInfluencer API (quota + monthly cache).

Agents invoke via ``terminal`` from Console-triggered gateway runs only.
LIVE gated commands require ``--campaign-config-file`` with a Console-signed
``nox_console_dispatch`` claim (see ``internal/console_dispatch.py``).
Never hand-roll ``noxinfluencer`` in SKILL prose.

Requires ``--env TEST|LIVE``. TEST uses fixtures and does not call Nox API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional

_PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PLUGIN_ROOT)

from internal import commands  # noqa: E402
from internal.audit_hooks import AuditContext  # noqa: E402
from internal.campaign_gate import (  # noqa: E402
    NoxCampaignGateError,
    load_campaign_config_file,
)
from internal.cli_runner import NoxCliError, NoxInsufficientCreditError  # noqa: E402
from internal.nox_auth import NoxAuthError  # noqa: E402
from internal.quota_ledger import QuotaExceededError  # noqa: E402
from internal.supplement_ledger import SupplementQuotaExceededError  # noqa: E402
from schemas import DEFAULT_MONTHLY_BUDGET, DEFAULT_TIMEZONE  # noqa: E402


def _print(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--env",
        required=True,
        choices=["TEST", "LIVE"],
        help="TEST=fixtures only; LIVE=real Nox API",
    )
    p.add_argument(
        "--campaign-config-file",
        help="JSON file with campaign_config (required for LIVE gated commands)",
    )
    p.add_argument(
        "--monthly-budget",
        type=int,
        default=DEFAULT_MONTHLY_BUDGET,
        help="Local monthly API call budget (default 1800)",
    )
    p.add_argument(
        "--timezone",
        default=os.environ.get("NOX_CACHE_TIMEZONE", DEFAULT_TIMEZONE),
        help="Cache month boundary timezone",
    )
    p.add_argument("--lang", default="en", choices=["en", "zh"])


def _add_audit(p: argparse.ArgumentParser) -> None:
    p.add_argument("--audit-campaign-id", help="CAL campaign_id for write-event audit")
    p.add_argument("--audit-identity-id", type=int, help="CAL identity_id for write-event audit")


def _audit_from_args(args: argparse.Namespace, *, gate: str, operation: str) -> Optional[AuditContext]:
    cid = getattr(args, "audit_campaign_id", None)
    iid = getattr(args, "audit_identity_id", None)
    if not cid or not iid:
        return None
    return AuditContext(
        campaign_id=str(cid),
        identity_id=int(iid),
        env=args.env,
        gate=gate,
        operation=operation,
    )


def _build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="nox_kol_tool")
    sub = root.add_subparsers(dest="cmd", required=True)

    qs = sub.add_parser("quota-snapshot", help="Local ledger + remote quota")
    _add_common(qs)
    qs.add_argument(
        "--refresh-remote",
        action="store_true",
        help="Bypass 5-minute remote quota cache",
    )
    qs.set_defaults(func=_cmd_quota_snapshot)

    dp = sub.add_parser("diligence-pack", help="Gate A: profile+audience+content bundle")
    _add_common(dp)
    _add_audit(dp)
    dp.add_argument("--gate", required=True)
    dp.add_argument("--nox-creator-id")
    dp.add_argument("--platform", choices=["youtube", "tiktok", "instagram"])
    dp.add_argument("--url")
    dp.add_argument("--channel-id")
    dp.add_argument(
        "--dimensions",
        default="profile,audience,content",
        help="Comma-separated dimensions",
    )
    dp.add_argument("--include-cooperation", action="store_true")
    dp.set_defaults(func=_cmd_diligence_pack)

    ct = sub.add_parser("contacts", help="Gate B: creator contacts")
    _add_common(ct)
    _add_audit(ct)
    ct.add_argument("--gate", required=True)
    ct.add_argument("--nox-creator-id")
    ct.add_argument("--platform", choices=["youtube", "tiktok", "instagram"])
    ct.add_argument("--url")
    ct.set_defaults(func=_cmd_contacts)

    cs = sub.add_parser("creator-search", help="Supplement search (1 page)")
    _add_common(cs)
    _add_audit(cs)
    cs.add_argument("--gate", required=True)
    cs.add_argument("--platform", required=True, choices=["youtube", "tiktok", "instagram"])
    cs.add_argument("--json", dest="body_json", help="Search body JSON string")
    cs.add_argument("--page-num", type=int, default=1)
    cs.set_defaults(func=_cmd_creator_search)

    ms = sub.add_parser("monitor-setup", help="Gate C: create+add-task")
    _add_common(ms)
    _add_audit(ms)
    ms.add_argument("--gate", required=True)
    ms.add_argument("--video-url", required=True)
    ms.add_argument("--project-id")
    ms.add_argument("--force", action="store_true")
    ms.set_defaults(func=_cmd_monitor_setup)

    st = sub.add_parser("cache-stats", help="Cache hits/misses + usage ledger")
    st.add_argument("--timezone", default=os.environ.get("NOX_CACHE_TIMEZONE", DEFAULT_TIMEZONE))
    st.add_argument("--campaign-id", help="Include supplement_usage for campaign")
    st.set_defaults(func=_cmd_cache_stats)

    dr = sub.add_parser(
        "doctor",
        help="Preflight: noxinfluencer on PATH, API key in config or Hermes .env",
    )
    dr.add_argument(
        "--env",
        required=True,
        choices=["TEST", "LIVE"],
        help="TEST=fixtures only; LIVE=check real auth",
    )
    dr.set_defaults(func=_cmd_doctor)

    return root


def _campaign_config(args: argparse.Namespace) -> dict:
    return load_campaign_config_file(getattr(args, "campaign_config_file", None))


def _cmd_quota_snapshot(args: argparse.Namespace) -> None:
    _print(
        commands.cmd_quota_snapshot(
            env=args.env,
            monthly_budget=args.monthly_budget,
            tz_name=args.timezone,
            refresh_remote=args.refresh_remote,
        )
    )


def _cmd_diligence_pack(args: argparse.Namespace) -> None:
    dims = [d.strip() for d in args.dimensions.split(",") if d.strip()]
    _print(
        commands.cmd_diligence_pack(
            env=args.env,
            gate=args.gate,
            monthly_budget=args.monthly_budget,
            tz_name=args.timezone,
            lang=args.lang,
            nox_creator_id=args.nox_creator_id,
            platform=args.platform,
            url=args.url,
            channel_id=args.channel_id,
            dimensions=dims,
            include_cooperation=args.include_cooperation,
            campaign_config=_campaign_config(args),
            audit=_audit_from_args(args, gate=args.gate, operation="diligence_pack"),
        )
    )


def _cmd_contacts(args: argparse.Namespace) -> None:
    if not args.nox_creator_id and not (args.platform and args.url):
        raise SystemExit("contacts requires --nox-creator-id or --platform + --url")
    _print(
        commands.cmd_contacts(
            env=args.env,
            gate=args.gate,
            monthly_budget=args.monthly_budget,
            tz_name=args.timezone,
            lang=args.lang,
            nox_creator_id=args.nox_creator_id or "",
            platform=args.platform,
            url=args.url,
            campaign_config=_campaign_config(args),
            audit=_audit_from_args(args, gate=args.gate, operation="contacts"),
        )
    )


def _cmd_creator_search(args: argparse.Namespace) -> None:
    body = json.loads(args.body_json) if args.body_json else {"page_num": args.page_num}
    _print(
        commands.cmd_creator_search(
            env=args.env,
            gate=args.gate,
            monthly_budget=args.monthly_budget,
            tz_name=args.timezone,
            lang=args.lang,
            platform=args.platform,
            body=body,
            page_num=args.page_num,
            campaign_config=_campaign_config(args),
            audit=_audit_from_args(args, gate=args.gate, operation="creator_search"),
        )
    )


def _cmd_monitor_setup(args: argparse.Namespace) -> None:
    _print(
        commands.cmd_monitor_setup(
            env=args.env,
            gate=args.gate,
            monthly_budget=args.monthly_budget,
            tz_name=args.timezone,
            lang=args.lang,
            video_url=args.video_url,
            project_id=args.project_id,
            force=args.force,
            campaign_config=_campaign_config(args),
            audit=_audit_from_args(args, gate=args.gate, operation="monitor_setup"),
        )
    )


def _cmd_cache_stats(args: argparse.Namespace) -> None:
    _print(
        commands.cmd_cache_stats(
            tz_name=args.timezone,
            campaign_id=getattr(args, "campaign_id", None),
        )
    )


def _cmd_doctor(args: argparse.Namespace) -> None:
    _print(commands.cmd_doctor(env=args.env))


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_parser().parse_args(argv)
    try:
        args.func(args)
    except QuotaExceededError as exc:
        _print({"success": False, "error_code": "NOX_QUOTA_EXCEEDED", "detail": str(exc)})
        raise SystemExit(2) from exc
    except SupplementQuotaExceededError as exc:
        _print({"success": False, "error_code": "NOX_SUPPLEMENT_EXCEEDED", "detail": str(exc)})
        raise SystemExit(4) from exc
    except NoxCampaignGateError as exc:
        _print({"success": False, "error_code": "NOX_CAMPAIGN_GATE", "detail": str(exc)})
        raise SystemExit(3) from exc
    except NoxInsufficientCreditError as exc:
        _print({
            "success": False,
            "error_code": "NOX_INSUFFICIENT_CREDIT",
            "detail": str(exc),
            "envelope": exc.envelope,
        })
        raise SystemExit(5) from exc
    except NoxCliError as exc:
        _print({"success": False, "error_code": "NOX_CLI_ERROR", "detail": str(exc)})
        raise SystemExit(1) from exc
    except NoxAuthError as exc:
        _print({"success": False, "error_code": "NOX_AUTH_MISSING", "detail": str(exc)})
        raise SystemExit(6) from exc


if __name__ == "__main__":
    main()
