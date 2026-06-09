"""Reconciler must not treat async ``pending:`` placeholders as gateway runs."""


def test_pending_run_id_guard_present_in_reconciler_source():
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "app/run_state_reconciler.py"
    ).read_text(encoding="utf-8")
    assert src.count("startswith(\"pending:\")") >= 3
