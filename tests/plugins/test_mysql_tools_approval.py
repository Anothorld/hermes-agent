"""Tests for mysql-tools plugin approval gate and direct SQL guard."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "plugins" / "mysql-tools"


def _load_module(relpath: str, name: str):
    path = PLUGIN_ROOT / relpath
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _approval_gate():
    return _load_module("internal/approval_gate.py", "mysql_tools_approval_gate_test")


def _guard():
    return _load_module("internal/direct_sql_guard.py", "mysql_tools_direct_sql_guard_test")


def _hooks():
    return _load_module("hooks.py", "mysql_tools_hooks_test")


class TestApprovalGate:
    def setup_method(self):
        gate = _approval_gate()
        gate.clear_session("sess-1")

    def test_session_skip_after_choice(self):
        gate = _approval_gate()
        ok, msg = gate.request_sql_execution_approval(
            sql="SELECT 1",
            database="ads",
            session_id="sess-1",
            clarify_callback=lambda *_: gate.CHOICE_SESSION_SKIP,
        )
        assert ok is True
        assert msg == ""
        assert gate.is_session_skipped("sess-1")

    def test_subsequent_calls_skip_clarify_in_same_session(self):
        gate = _approval_gate()
        clarify_calls = []

        def _clarify(*_args, **_kwargs):
            clarify_calls.append(1)
            return gate.CHOICE_SESSION_SKIP

        ok1, _ = gate.request_sql_execution_approval(
            sql="SELECT 1",
            database=None,
            session_id="sess-1",
            clarify_callback=_clarify,
        )
        assert ok1 is True
        assert len(clarify_calls) == 1

        def _should_not_run(*_args, **_kwargs):
            raise AssertionError("clarify should be skipped for this session")

        ok2, _ = gate.request_sql_execution_approval(
            sql="SELECT 2",
            database=None,
            session_id="sess-1",
            clarify_callback=_should_not_run,
        )
        assert ok2 is True
        assert len(clarify_calls) == 1

    def test_numeric_session_skip_choice(self):
        gate = _approval_gate()
        ok, _ = gate.request_sql_execution_approval(
            sql="SELECT 1",
            database=None,
            session_id="sess-3",
            clarify_callback=lambda *_: "3",
        )
        assert ok is True
        assert gate.is_session_skipped("sess-3")

    def test_clear_session_removes_skip(self):
        gate = _approval_gate()
        gate.mark_session_skipped("sess-clear")
        assert gate.is_session_skipped("sess-clear")
        gate.clear_session("sess-clear")
        assert not gate.is_session_skipped("sess-clear")

    def test_timeout_auto_rejects_gateway_style(self):
        gate = _approval_gate()
        ok, msg = gate.request_sql_execution_approval(
            sql="SELECT 1",
            database=None,
            session_id="sess-timeout-gw",
            clarify_callback=lambda *_: "[user did not respond within 10m]",
        )
        assert ok is False
        assert "超时" in msg

    def test_timeout_auto_rejects_cli_style(self):
        gate = _approval_gate()
        ok, msg = gate.request_sql_execution_approval(
            sql="SELECT 1",
            database=None,
            session_id="sess-timeout-cli",
            clarify_callback=lambda *_: (
                "The user did not provide a response within the time limit. "
                "Use your best judgement to make the choice and proceed."
            ),
        )
        assert ok is False
        assert "超时" in msg

    def test_empty_clarify_response_auto_rejects(self):
        gate = _approval_gate()
        ok, msg = gate.request_sql_execution_approval(
            sql="SELECT 1",
            database=None,
            session_id="sess-timeout-empty",
            clarify_callback=lambda *_: "",
        )
        assert ok is False
        assert "超时" in msg

    def test_deny(self):
        gate = _approval_gate()
        ok, msg = gate.request_sql_execution_approval(
            sql="SELECT 1",
            database=None,
            session_id="sess-2",
            clarify_callback=lambda *_: gate.CHOICE_DENY,
        )
        assert ok is False
        assert "拒绝" in msg

    def test_fail_closed_without_callback(self):
        gate = _approval_gate()
        original = gate._get_resolve_clarify_callback

        def _no_callback():
            return lambda explicit=None: None

        gate._get_resolve_clarify_callback = _no_callback
        try:
            ok, msg = gate.request_sql_execution_approval(
                sql="SELECT 1",
                database="ads",
                session_id="sess-4",
                clarify_callback=None,
            )
        finally:
            gate._get_resolve_clarify_callback = original
        assert ok is False
        assert "clarify" in msg

class TestDirectSqlGuard:
    def test_blocks_sql_executor_run(self):
        guard = _guard()
        assert guard.is_blocked_terminal_command(
            "python mysql_tools/sql_executor.py --sql 'SELECT 1'"
        )

    def test_allows_validate_and_test(self):
        guard = _guard()
        assert not guard.is_blocked_terminal_command(
            "python mysql_tools/sql_executor.py --validate --sql 'SELECT 1'"
        )
        assert not guard.is_blocked_terminal_command(
            "python mysql_tools/sql_executor.py --test"
        )

    def test_blocks_mysql_cli(self):
        guard = _guard()
        assert guard.is_blocked_terminal_command("mysql -h host -u u -p'x' -e 'SELECT 1'")

    def test_blocks_python_import_bypass(self):
        guard = _guard()
        assert guard.is_blocked_execute_code("import sql_executor\nprint(sql_executor.execute_query('SELECT 1'))")

    def test_allows_schema_reader_in_execute_code(self):
        guard = _guard()
        assert not guard.is_blocked_execute_code("import schema_reader\nschema_reader.fetch_all_tables()")


class TestTerminalHook:
    def test_pre_tool_call_blocks_terminal(self):
        hooks = _hooks()
        result = hooks.pre_tool_call(
            "terminal",
            {"command": "python sql_executor.py --sql 'SELECT 1'"},
        )
        assert result is not None
        assert result["action"] == "block"
        assert "mysql_execute_sql" in result["message"]

    def test_pre_tool_call_blocks_execute_code(self):
        hooks = _hooks()
        result = hooks.pre_tool_call(
            "execute_code",
            {"code": "import pymysql\npymysql.connect(host='x').cursor().execute('SELECT 1')"},
        )
        assert result is not None
        assert result["action"] == "block"
