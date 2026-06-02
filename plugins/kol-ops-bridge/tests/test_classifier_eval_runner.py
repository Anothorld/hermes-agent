"""Tests for classifier golden-set runner."""

from __future__ import annotations

from pathlib import Path


def test_classifier_eval_runner_passes_bundled_cases(bridge_pkg):
    cer = bridge_pkg.classifier_eval_runner
    cases_dir = Path(__file__).resolve().parents[1] / "eval" / "cases"
    report = cer.run_deterministic_eval(cases_dir=cases_dir)
    assert report["total"] > 0
    assert report["failed"] == 0, report.get("failures")
