"""Tests for KOL discovery statistics CLI subcommands."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"
CLI = SCRIPTS / "kol_bridge_tool.py"


def _load_metrics_module():
    path = SCRIPTS / "_subcmd_metrics.py"
    spec = importlib.util.spec_from_file_location("_subcmd_metrics_test", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cli_registers_discovery_metric_subcommands():
    proc = subprocess.run(
        [sys.executable, str(CLI), "get-discovery-stats", "-h"],
        cwd=PLUGIN_ROOT.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    for name in (
        "get-discovery-summary",
        "get-discovery-summary-trend",
        "get-discovery-funnel",
        "list-kol-registry",
        "get-discovery-stats",
    ):
        help_proc = subprocess.run(
            [sys.executable, str(CLI), name, "-h"],
            cwd=PLUGIN_ROOT.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        assert help_proc.returncode == 0, name


def test_get_discovery_stats_batches_sections():
    mod = _load_metrics_module()
    client = MagicMock()
    client.request.side_effect = [
        {"env": "LIVE", "discovered_total": 10},
        {"env": "LIVE", "series": {"discovered_total": []}},
    ]
    args = argparse_namespace(
        env="LIVE",
        sections="summary,trend",
        bucket="week",
        periods=4,
        days=None,
        q=None,
        source="all",
        sort="ingested_at",
        order="desc",
        limit=50,
        offset=0,
    )
    with patch.object(mod, "client_from_args", return_value=client), patch.object(
        mod, "print_json",
    ) as print_json:
        mod.cmd_get_discovery_stats(args)

    assert client.request.call_count == 2
    print_json.assert_called_once()
    payload = print_json.call_args[0][0]
    assert payload["env"] == "LIVE"
    assert "summary" in payload["sections"]
    assert "trend" in payload["sections"]


def test_get_discovery_stats_rejects_unknown_section():
    mod = _load_metrics_module()
    args = argparse_namespace(
        env="LIVE",
        sections="summary,unknown",
        bucket="week",
        periods=None,
        days=None,
        q=None,
        source="all",
        sort="ingested_at",
        order="desc",
        limit=50,
        offset=0,
    )
    with patch.object(mod, "client_from_args", return_value=MagicMock()):
        with pytest.raises(SystemExit):
            mod.cmd_get_discovery_stats(args)


def argparse_namespace(**kwargs):
    class _NS:
        pass

    ns = _NS()
    for key, value in kwargs.items():
        setattr(ns, key, value)
    return ns
