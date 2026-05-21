#!/usr/bin/env python3
"""Deterministic CLI wrapper for the ``kol-ops-bridge`` HTTP API (v2.4).

Skill / agent-facing tool. **Never** opens the CAL SQLite directly; every
subcommand is a thin shell over the plugin HTTP endpoints exposed by
``serve.py`` (default ``http://127.0.0.1:8765``). Designed so that
deterministic CRUD-like operations (writing facts, opening escalations,
selecting candidates, archiving collabs) can be invoked from a SKILL.md
``Procedure`` section without letting an LLM hand-roll SQL.

Auth: either pass ``--bridge-key`` or set ``HERMES_KOL_OPS_BRIDGE_KEY``
in the environment. Open mode (no key) is supported for dev only — the
plugin will refuse mutating admin endpoints in that case.

All env-aware mutating subcommands require an explicit ``--env`` value
(``TEST`` or ``LIVE``); we never default it.

Subcommands intentionally minimal — covers Phase B-α (router + classifier
+ dispatcher). Additional subcommands (write-facts batch, parse-campaign-
intent, archival flow) added incrementally as later phases ship.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional


_DEFAULT_BASE = os.environ.get(
    "HERMES_KOL_OPS_BRIDGE_BASE", "http://127.0.0.1:8765"
).rstrip("/")
_KEY_ENV = "HERMES_KOL_OPS_BRIDGE_KEY"


# --------------------------------------------------------------------- HTTP
def _request(
    method: str,
    path: str,
    *,
    base: str,
    bridge_key: Optional[str],
    params: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    url = f"{base}{path}"
    if params:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    data: Optional[bytes] = None
    headers: dict[str, str] = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if bridge_key:
        headers["X-Bridge-Key"] = bridge_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(
            json.dumps({"error": "http_error", "status": exc.code, "detail": detail})
        )
    except urllib.error.URLError as exc:
        raise SystemExit(
            json.dumps({"error": "bridge_unreachable", "detail": str(exc.reason)})
        )
    if not payload:
        return {}
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError:
        return {"_raw": payload.decode("utf-8", "replace")}


# ------------------------------------------------------------- arg helpers
def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--base", default=_DEFAULT_BASE,
                   help="Bridge HTTP base URL (default %(default)s)")
    p.add_argument("--bridge-key", default=os.environ.get(_KEY_ENV),
                   help=f"X-Bridge-Key header value (env {_KEY_ENV})")


def _parse_json_arg(val: Optional[str], *, required: bool = True) -> dict[str, Any]:
    if not val:
        if required:
            raise SystemExit(json.dumps({"error": "missing_json"}))
        return {}
    try:
        out = json.loads(val)
    except json.JSONDecodeError as exc:
        raise SystemExit(json.dumps({"error": "bad_json", "detail": str(exc)}))
    if not isinstance(out, dict):
        raise SystemExit(json.dumps({"error": "json_must_be_object"}))
    return out


def _print(out: Any) -> None:
    print(json.dumps(out, ensure_ascii=False, indent=2))


# --------------------------------------------------------------- handlers
def cmd_health(args: argparse.Namespace) -> None:
    _print(_request("GET", "/health", base=args.base, bridge_key=args.bridge_key))


def cmd_write_facts(args: argparse.Namespace) -> None:
    body = _parse_json_arg(args.json)
    body.setdefault("env", args.env)
    if "facts" not in body or not isinstance(body["facts"], dict):
        raise SystemExit(json.dumps({"error": "json_missing_facts_dict"}))
    _print(_request(
        "POST", f"/facts/{args.identity_id}",
        base=args.base, bridge_key=args.bridge_key, body=body,
    ))


def cmd_write_facts_multi(args: argparse.Namespace) -> None:
    body = _parse_json_arg(args.json)
    body.setdefault("env", args.env)
    if "namespaces" not in body or not isinstance(body["namespaces"], dict):
        raise SystemExit(json.dumps({"error": "json_missing_namespaces_dict"}))
    _print(_request(
        "POST", f"/facts/{args.identity_id}/multi",
        base=args.base, bridge_key=args.bridge_key, body=body,
    ))


def cmd_get_dispatch_context(args: argparse.Namespace) -> None:
    _print(_request(
        "GET", f"/identities/{args.identity_id}/dispatch-context",
        base=args.base, bridge_key=args.bridge_key,
        params={"campaign_id": args.campaign_id, "env": args.env},
    ))


def cmd_get_goals(args: argparse.Namespace) -> None:
    _print(_request(
        "GET", f"/identities/{args.identity_id}/goals",
        base=args.base, bridge_key=args.bridge_key,
        params={"campaign_id": args.campaign_id, "env": args.env},
    ))


def cmd_get_lanes(args: argparse.Namespace) -> None:
    _print(_request(
        "GET", f"/campaigns/{args.campaign_id}/lanes",
        base=args.base, bridge_key=args.bridge_key, params={"env": args.env},
    ))


def cmd_get_relationship(args: argparse.Namespace) -> None:
    _print(_request(
        "GET", f"/identities/{args.identity_id}/relationship",
        base=args.base, bridge_key=args.bridge_key,
    ))


def cmd_get_reusable_facts(args: argparse.Namespace) -> None:
    _print(_request(
        "GET", f"/identities/{args.identity_id}/relationship/reusable-facts",
        base=args.base, bridge_key=args.bridge_key,
    ))


def cmd_list_candidates(args: argparse.Namespace) -> None:
    _print(_request(
        "GET", f"/campaigns/{args.campaign_id}/candidates",
        base=args.base, bridge_key=args.bridge_key, params={"env": args.env},
    ))


def cmd_resolve_relationships(args: argparse.Namespace) -> None:
    _print(_request(
        "POST",
        f"/campaigns/{args.campaign_id}/candidates/resolve-relationships",
        base=args.base, bridge_key=args.bridge_key, params={"env": args.env},
    ))


def cmd_select_candidates(args: argparse.Namespace) -> None:
    body = _parse_json_arg(args.json)
    body.setdefault("env", args.env)
    if "identity_ids" not in body or not isinstance(body["identity_ids"], list):
        raise SystemExit(json.dumps({"error": "json_missing_identity_ids_list"}))
    body.setdefault("selected_by", args.selected_by or "agent")
    _print(_request(
        "POST", f"/campaigns/{args.campaign_id}/candidates/select",
        base=args.base, bridge_key=args.bridge_key, body=body,
    ))


def cmd_open_escalation(args: argparse.Namespace) -> None:
    body = _parse_json_arg(args.json)
    body.setdefault("env", args.env)
    for required in ("identity_id", "campaign_id", "goal", "reason"):
        if required not in body:
            raise SystemExit(
                json.dumps({"error": f"json_missing_{required}"})
            )
    _print(_request(
        "POST", "/escalations",
        base=args.base, bridge_key=args.bridge_key, body=body,
    ))


def cmd_resolve_escalation(args: argparse.Namespace) -> None:
    body = _parse_json_arg(args.json)
    body.setdefault("env", args.env)
    if "final_state" not in body:
        raise SystemExit(json.dumps({"error": "json_missing_final_state"}))
    _print(_request(
        "PATCH", f"/escalations/{args.escalation_id}",
        base=args.base, bridge_key=args.bridge_key, body=body,
    ))


def cmd_route_discovery(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "env": args.env,
        "selected_by": args.selected_by or "agent",
        "operator_note": args.operator_note or "",
    }
    _print(_request(
        "POST", f"/campaigns/{args.campaign_id}/candidates/route-discovery",
        base=args.base, bridge_key=args.bridge_key, body=body,
    ))


# ------------------------------------------------------------------- main
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kol_bridge_tool",
        description="Deterministic CLI for hermes kol-ops-bridge (v2.4).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pH = sub.add_parser("health", help="Bridge healthcheck.")
    _add_common(pH)
    pH.set_defaults(func=cmd_health)

    pWF = sub.add_parser(
        "write-facts",
        help=("Write facts for an identity in a campaign. "
              "JSON body must contain {facts: {<namespace>.<key>: <val>, ...}, "
              "campaign_id, namespace, source}."),
    )
    _add_common(pWF)
    pWF.add_argument("--identity-id", type=int, required=True)
    pWF.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pWF.add_argument("--json", required=True,
                     help="JSON body for POST /facts/{identity_id}")
    pWF.set_defaults(func=cmd_write_facts)

    pWFM = sub.add_parser(
        "write-facts-multi",
        help=("Write facts across multiple namespaces in one call. "
              "JSON body must contain {namespaces: {<ns>: {<ns>.<key>: <val>, ...}}, "
              "campaign_id, source}. Replaces N back-to-back write-facts calls."),
    )
    _add_common(pWFM)
    pWFM.add_argument("--identity-id", type=int, required=True)
    pWFM.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pWFM.add_argument("--json", required=True,
                      help="JSON body for POST /facts/{identity_id}/multi")
    pWFM.set_defaults(func=cmd_write_facts_multi)

    pGDC = sub.add_parser(
        "get-dispatch-context",
        help=("Bundle goal_state + lanes + relationship + reusable_facts "
              "for one (identity, campaign). One call replaces 4."),
    )
    _add_common(pGDC)
    pGDC.add_argument("--identity-id", type=int, required=True)
    pGDC.add_argument("--campaign-id", required=True)
    pGDC.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pGDC.set_defaults(func=cmd_get_dispatch_context)

    pGG = sub.add_parser("get-goals",
                         help="Get the 10 goal states for one identity in one campaign.")
    _add_common(pGG)
    pGG.add_argument("--identity-id", type=int, required=True)
    pGG.add_argument("--campaign-id", required=True)
    pGG.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pGG.set_defaults(func=cmd_get_goals)

    pGL = sub.add_parser("get-lanes",
                         help="Get the lane view (commerce/fulfillment/publish/meta) for a campaign.")
    _add_common(pGL)
    pGL.add_argument("--campaign-id", required=True)
    pGL.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pGL.set_defaults(func=cmd_get_lanes)

    pGR = sub.add_parser("get-relationship",
                         help="Read the identity-level relationship row.")
    _add_common(pGR)
    pGR.add_argument("--identity-id", type=int, required=True)
    pGR.set_defaults(func=cmd_get_relationship)

    pGRF = sub.add_parser("get-reusable-facts",
                          help="Read identity-level facts that can be reused across campaigns.")
    _add_common(pGRF)
    pGRF.add_argument("--identity-id", type=int, required=True)
    pGRF.set_defaults(func=cmd_get_reusable_facts)

    pLC = sub.add_parser("list-candidates",
                         help="List candidate KOLs in a campaign discovery pool.")
    _add_common(pLC)
    pLC.add_argument("--campaign-id", required=True)
    pLC.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pLC.set_defaults(func=cmd_list_candidates)

    pRR = sub.add_parser("resolve-relationships",
                         help="Re-run identity-resolution + relationship-status pass on the discovery pool.")
    _add_common(pRR)
    pRR.add_argument("--campaign-id", required=True)
    pRR.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pRR.set_defaults(func=cmd_resolve_relationships)

    pSC = sub.add_parser(
        "select-candidates",
        help=("Move candidates from the discovery pool into selected_for_outreach. "
              "JSON body must contain {identity_ids: [..]}; selected_by optional."),
    )
    _add_common(pSC)
    pSC.add_argument("--campaign-id", required=True)
    pSC.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pSC.add_argument("--selected-by", default="agent")
    pSC.add_argument("--json", required=True)
    pSC.set_defaults(func=cmd_select_candidates)

    pOE = sub.add_parser(
        "open-escalation",
        help=("Open an escalation. JSON body must contain "
              "{identity_id, campaign_id, goal, reason} plus optional {operator_note}."),
    )
    _add_common(pOE)
    pOE.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pOE.add_argument("--json", required=True)
    pOE.set_defaults(func=cmd_open_escalation)

    pRE = sub.add_parser(
        "resolve-escalation",
        help=("Resolve an escalation. JSON body must contain "
              "{final_state: resolved|re_escalated|aborted|answered} + optional fields."),
    )
    _add_common(pRE)
    pRE.add_argument("--escalation-id", type=int, required=True)
    pRE.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pRE.add_argument("--json", required=True)
    pRE.set_defaults(func=cmd_resolve_escalation)

    pRD = sub.add_parser(
        "route-discovery",
        help=("Deterministic Discovery → Outreach router. Resolves "
              "relationships, marks new_prospect/repeat_kol candidates "
              "selected_for_outreach with identity.outreach_path facts, "
              "and opens escalations for repeat_kol_needs_review."),
    )
    _add_common(pRD)
    pRD.add_argument("--campaign-id", required=True)
    pRD.add_argument("--env", required=True, choices=("TEST", "LIVE"))
    pRD.add_argument("--selected-by", default="agent")
    pRD.add_argument("--operator-note", default="",
                     help="One-line summary attached to any opened escalations.")
    pRD.set_defaults(func=cmd_route_discovery)

    return p


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(json.dumps({"error": "unexpected", "detail": str(exc)}))
        raise
