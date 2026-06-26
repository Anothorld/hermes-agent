"""Tests for kol-bridge-agent-guard pre_tool_call hook."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _hooks():
    path = PLUGIN_ROOT / "hooks.py"
    spec = importlib.util.spec_from_file_location("kol_bridge_agent_guard_hooks_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_blocks_execute_code_curl():
    h = _hooks()
    out = h.pre_tool_call(
        "execute_code",
        {"code": 'BRIDGE_KEY = "x"\ncurl http://127.0.0.1:8080/api/plugins/kol-ops-bridge/health'},
        session_id="kol-campaign:LIVE:SEB",
    )
    assert out is not None
    assert out["action"] == "block"
    payload = json.loads(out["message"])
    assert payload["error"] == "bridge_agent_contract_violation"


def test_blocks_bare_python_bridge_cli():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "get-escalation --escalation-id 108 --env LIVE"
            ),
        },
        session_id="kol-campaign:LIVE:SEB",
    )
    assert out is not None
    assert out["action"] == "block"


def test_allows_terminal_python3_u_kol_bridge_tool():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "get-escalation --escalation-id 108 --env LIVE"
            ),
        },
        session_id="kol-campaign:LIVE:SEB",
    )
    assert out is None


def test_allows_terminal_kol_bridge_cli_wrapper():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "/Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli "
                "get-escalation --escalation-id 108 --env LIVE"
            ),
        },
        session_id="kol-campaign:LIVE:SEB",
    )
    assert out is None


def test_blocks_redirect_bridge_read_stdout():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "get-campaign --campaign-id SEB8008-20260525 --env LIVE > /tmp/c.json"
            ),
        },
        session_id="kol-campaign-draft:LIVE:SEB8008-20260525:820",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_redirect_list_candidates_stdout():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "list-candidates --campaign-id SSF8033-20260609 --env LIVE > /tmp/candidates.json"
            ),
        },
        session_id="kol-campaign:LIVE:SSF8033-20260609",
    )
    assert out is not None
    assert out["action"] == "block"
    payload = json.loads(out["message"])
    assert payload["error"] == "bridge_agent_contract_violation"
    assert payload.get("source") == "kol_bridge_agent_guard"


def test_blocks_pipe_head_on_bridge_cli():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "list-candidates --campaign-id X --env LIVE | head"
            ),
        },
        session_id="kol-campaign:LIVE:SSF8033-20260609",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_read_identity_hallucination():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "read-identity --identity-id 1 --env LIVE"
            ),
        },
        session_id="kol-campaign:LIVE:SSF8033-20260609",
    )
    assert out is not None
    assert json.loads(out["message"])["code"] == "invalid_subcommand_read_identity"


def test_allows_single_ingest_confirmed_candidate_terminal():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "ingest-confirmed-candidate --campaign-id SSF8033-20260609 --env LIVE "
                "--json @/tmp/ingest_dressyourdecor.json"
            ),
        },
        session_id="kol-campaign:LIVE:SSF8033-20260609",
    )
    assert out is None


def test_blocks_terminal_multi_ingest_semicolon():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "ingest-confirmed-candidate --env LIVE --json @/tmp/ingest_a.json; "
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol_bridge_tool.py "
                "ingest-confirmed-candidate --env LIVE --json @/tmp/ingest_b.json"
            ),
        },
        session_id="kol-campaign:LIVE:SSF8033-20260609",
    )
    assert out is not None
    assert json.loads(out["message"])["code"] == "terminal_multi_ingest"


def test_blocks_python3_u_on_kol_bridge_cli_wrapper():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -u /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli "
                "get-campaign --campaign-id SEB8008-20260525 --env LIVE"
            ),
        },
        session_id="kol-campaign-draft:LIVE:SEB8008-20260525:820",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_wrong_nox_tool_path_on_terminal():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 /Users/me/agent_prj/hermes-agent/plugins/kol-ops-bridge/"
                "scripts/nox_kol_tool.py contacts --env LIVE"
            ),
        },
        task_id="kol-email-discover:LIVE:501:pending:abc",
    )
    assert out is not None
    assert out["action"] == "block"
    payload = json.loads(out["message"])
    assert payload["code"] == "wrong_nox_tool_path"
    assert "nox-kol-bridge" in payload["hint"]


def test_blocks_browser_on_outreach_session():
    h = _hooks()
    out = h.pre_tool_call(
        "browser_navigate",
        {"url": "https://www.instagram.com/foo/"},
        task_id="kol-campaign-outreach:LIVE:POVISON-TS-8319",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_delegate_on_creator_brief_refresh_session():
    h = _hooks()
    out = h.pre_tool_call(
        "delegate_task",
        {"prompt": "find email"},
        task_id="kol-creator-brief-refresh:LIVE:701:pending:abc",
    )
    assert out is not None
    assert out["action"] == "block"


def test_allows_browser_on_creator_brief_refresh_session():
    h = _hooks()
    out = h.pre_tool_call(
        "browser_navigate",
        {"url": "https://www.instagram.com/foo/"},
        task_id="kol-creator-brief-refresh:LIVE:701:pending:abc",
    )
    assert out is None


def test_blocks_mcp_chrome_on_kol_session_via_task_id():
    h = _hooks()
    out = h.pre_tool_call(
        "mcp_chrome_devtools_navigate_page",
        {"url": "https://www.instagram.com/foo/"},
        task_id="kol-email-discover:LIVE:701",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "mcp_chrome_devtools" in out["message"]


def test_blocks_mcp_chrome_on_campaign_draft_session():
    """Regression: ``kol-campaign-draft:`` must also block mcp_chrome.

    The old ``kol-campaign:`` (trailing colon) prefix did not match
    ``kol-campaign-draft:LIVE:...``, so redraft runs could loop on the dead
    chrome-devtools MCP endpoint (POVISON stuck-run incident).
    """
    h = _hooks()
    for task in (
        "kol-campaign-draft:LIVE:POVISON-TS-8319-20260603",
        "kol-campaign-outreach:LIVE:POVISON-TS-8319",
    ):
        out = h.pre_tool_call(
            "mcp_chrome_devtools_navigate_page",
            {"url": "https://example.com/"},
            task_id=task,
        )
        assert out is not None, task
        assert out["action"] == "block"
        assert "mcp_chrome_devtools" in out["message"]


def test_allows_browser_on_email_discover_session():
    h = _hooks()
    out = h.pre_tool_call(
        "browser_navigate",
        {"url": "https://www.instagram.com/foo/"},
        task_id="kol-email-discover:LIVE:701",
    )
    assert out is None


def test_blocks_veedcrawl_on_email_discover():
    h = _hooks()
    for tool in (
        "veedcrawl_instagram_profile",
        "veedcrawl_search_social_videos",
        "veedcrawl_extract",
    ):
        out = h.pre_tool_call(
            tool,
            {"username": "tammymerecka"},
            task_id="kol-email-discover:LIVE:701",
        )
        assert out is not None, tool
        assert out["action"] == "block"
        assert "veedcrawl" in out["message"].lower()


def test_blocks_delegate_task_on_email_discover():
    h = _hooks()
    out = h.pre_tool_call(
        "delegate_task",
        {"task": "find email for tammymerecka"},
        task_id="kol-email-discover:LIVE:701",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "browser_navigate" in out["message"]


def test_blocks_delegate_task_on_campaign_discovery():
    h = _hooks()
    out = h.pre_tool_call(
        "delegate_task",
        {
            "goal": "Search the public web for 150 Instagram handles",
            "toolsets": ["web", "veedcrawl"],
        },
        task_id="kol-campaign:LIVE:SEB8010-20260608",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "browser_navigate" in out["message"]
    assert "delegate_task" in out["message"].lower()


def test_allows_delegate_task_on_outreach_session():
    """Outreach runs may still use other tools; delegate is not blanket-blocked."""
    h = _hooks()
    out = h.pre_tool_call(
        "delegate_task",
        {"goal": "draft follow-up"},
        task_id="kol-campaign-outreach:LIVE:POVISON-TS-8319",
    )
    assert out is None


def test_blocks_execute_code_browser_workaround_on_email_discover():
    h = _hooks()
    out = h.pre_tool_call(
        "execute_code",
        {"code": "from hermes_tools import terminal\n# browser_navigate ig"},
        task_id="kol-email-discover:LIVE:701",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_web_search_on_email_discover():
    """Tier 1 must use browser Google, not web_search/web_extract."""
    h = _hooks()
    for tool in ("web_search", "web_extract"):
        out = h.pre_tool_call(
            tool,
            {"query": "tammymerecka email contact"},
            task_id="kol-email-discover:LIVE:701",
        )
        assert out is not None, tool
        assert out["action"] == "block"
        assert "google.com/search" in out["message"]


def test_blocks_terminal_duckduckgo_scrape_on_email_discover():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "curl -sL 'https://html.duckduckgo.com/html/?q=tammymerecka+email' "
                "-o /tmp/ddg.html"
            ),
        },
        task_id="kol-email-discover:LIVE:701",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_terminal_urllib_fetch_on_email_discover():
    """Regression (POVISON 701): model used `terminal python3 urllib` to scrape
    beacons.ai/bio.link/Instagram instead of web_extract / browser_navigate."""
    h = _hooks()
    for cmd in (
        "python3 -c \"import urllib.request; urllib.request.urlopen('https://beacons.ai/tammymerecka')\"",
        "python3 -c \"import requests; requests.get('https://bio.link/tammymerecka')\"",
        "wget -q https://www.instagram.com/tammymerecka/ -O /tmp/ig.html",
    ):
        out = h.pre_tool_call(
            "terminal", {"command": cmd}, task_id="kol-email-discover:LIVE:701"
        )
        assert out is not None, cmd
        assert out["action"] == "block"
        assert "browser_navigate" in out["message"] or "browser_google" in out["message"] or "google.com/search" in out["message"]


def test_allows_bridge_cli_terminal_on_email_discover():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "/Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli "
                "get-identity --identity-id 701 --env LIVE"
            ),
        },
        task_id="kol-email-discover:LIVE:701",
    )
    assert out is None


def test_allows_browser_on_discovery_session():
    h = _hooks()
    ds = h._load_discovery_session()
    sid = "kol-campaign:LIVE:POVISON-TS-8319"
    ds.reset_bootstrap(sid)
    cli = "/Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli"
    for cmd in (
        f"{cli} list-candidates --env LIVE --campaign-id POVISON-TS-8319",
        f"{cli} list-discovery-skip-handles --env LIVE",
        f"{cli} list-outreach-cooldown-handles --env LIVE --plain",
    ):
        assert h.pre_tool_call("terminal", {"command": cmd}, task_id=sid) is None
    out = h.pre_tool_call(
        "browser_navigate",
        {"url": "https://www.instagram.com/foo/"},
        task_id=sid,
    )
    assert out is None


def test_blocks_web_search_on_campaign_discovery():
    h = _hooks()
    for tool in ("web_search", "web_extract"):
        out = h.pre_tool_call(
            tool,
            {"query": "home cinema instagram creator 100k"},
            task_id="kol-campaign:LIVE:SSF8033-20260609",
        )
        assert out is not None, tool
        assert out["action"] == "block"
        assert "google.com/search" in out["message"]


def test_blocks_terminal_serper_on_campaign_discovery():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "python3 -c \"import requests; requests.get("
                "'https://google.serper.dev/search', params={'q': 'US home cinema instagram'})\""
            ),
        },
        task_id="kol-campaign:LIVE:SSF8033-20260609",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "browser_navigate" in out["message"]


def test_blocks_execute_code_requests_serper_on_campaign_discovery():
    h = _hooks()
    out = h.pre_tool_call(
        "execute_code",
        {
            "code": (
                "import requests\n"
                "requests.get('https://google.serper.dev/search', params={'q': 'cozy living'})"
            ),
        },
        task_id="kol-campaign:LIVE:SSF8033-20260609",
    )
    assert out is not None
    assert out["action"] == "block"


def test_allows_bridge_cli_terminal_on_campaign_discovery():
    h = _hooks()
    ds = h._load_discovery_session()
    sid = "kol-campaign:LIVE:SSF8033-20260609"
    ds.reset_bootstrap(sid)
    cli = "/Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli"
    for cmd in (
        f"{cli} list-candidates --env LIVE --campaign-id SSF8033-20260609",
        f"{cli} list-discovery-skip-handles --env LIVE",
        f"{cli} list-outreach-cooldown-handles --env LIVE --plain",
    ):
        out = h.pre_tool_call("terminal", {"command": cmd}, task_id=sid)
        assert out is None, cmd
    out = h.pre_tool_call(
        "browser_navigate",
        {"url": "https://www.instagram.com/example/"},
        task_id=sid,
    )
    assert out is None


def test_blocks_browser_before_discovery_bootstrap():
    h = _hooks()
    ds = h._load_discovery_session()
    sid = "kol-campaign:LIVE:SSF8033-20260609"
    ds.reset_bootstrap(sid)
    out = h.pre_tool_call(
        "browser_navigate",
        {"url": "https://www.instagram.com/example/"},
        task_id=sid,
    )
    assert out is not None
    assert out["action"] == "block"
    assert "bootstrap incomplete" in out["message"].lower()


def test_blocks_wrong_campaign_id_on_discovery_session():
    h = _hooks()
    out = h.pre_tool_call(
        "terminal",
        {
            "command": (
                "/Users/me/hermes-agent/plugins/kol-ops-bridge/scripts/kol-bridge-cli "
                "list-candidates --env LIVE --campaign-id POVISON-TS-8319-20260603"
            ),
        },
        task_id="kol-campaign:LIVE:SEB8010-20260608",
    )
    assert out is not None
    assert out["action"] == "block"
    assert "does not match" in out["message"]


def test_blocks_read_plugin_api_on_kol_session():
    h = _hooks()
    out = h.pre_tool_call(
        "read_file",
        {"path": "plugins/kol-ops-bridge/plugin_api.py"},
        session_id="kol-reply:LIVE:667:msg",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_read_env_file():
    h = _hooks()
    out = h.pre_tool_call(
        "read_file",
        {"path": "/Users/me/.hermes/profiles/kol-orchestrator/.env"},
        session_id="kol-campaign:LIVE:C1",
    )
    assert out is not None
    assert out["action"] == "block"


def test_blocks_write_facts_reply_draft_in_execute_code():
    h = _hooks()
    out = h.pre_tool_call(
        "execute_code",
        {
            "code": (
                'write-facts-multi --json {"namespaces":'
                '{"approval": {"approval.reply_draft": {}}}}'
            ),
        },
        session_id="kol-campaign:LIVE:C1",
    )
    assert out is not None


def test_pre_tool_call_accepts_observer_kwargs():
    """Hermes passes turn_id/api_request_id/middleware_trace since 0.14 — guard must not crash."""
    h = _hooks()
    out = h.pre_tool_call(
        "skill_view",
        {"name": "kol-reply-dispatcher"},
        session_id="kol-reply:LIVE:862:msg",
        turn_id="turn-abc",
        api_request_id="req-xyz",
        middleware_trace=[{"kind": "test"}],
    )
    assert out is None
