#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline regression tests for the bounded Runtime protocol."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
REGRESSION = ROOT / "evals" / "regression"
if str(REGRESSION) not in sys.path:
    sys.path.insert(0, str(REGRESSION))

import runtime_protocol as rp  # noqa: E402


class RuntimeProtocolTests(unittest.TestCase):
    def test_builtin_protocol_cases_are_green(self):
        results = rp.run_builtin_runtime_cases()
        self.assertEqual([result.case_id for result in results], [
            "support-overreach-repaired",
            "reasonable-support-wording",
            "duplicate-warning-local-review",
            "warning-remains-pass",
            "repair-budget-exhausted",
        ])
        for result in results:
            self.assertTrue(result.passed, f"{result.case_id}: {result.problems}")

    def test_warning_only_trace_passes(self):
        trace = rp.RuntimeTrace(
            states=rp._base_states(), draft_count=1, validator_runs=1,
            repair_rounds=0, remaining_errors=0, remaining_warnings=3,
            runtime_pass=True,
        )
        self.assertTrue(rp.check_trace(trace).passed)

    def test_two_repairs_with_error_must_terminate(self):
        trace = rp.RuntimeTrace(
            states=rp._base_states(repairs=2, include_delivery=False),
            draft_count=1, validator_runs=3, repair_rounds=2,
            remaining_errors=1, remaining_warnings=0, runtime_pass=False,
            termination_reason="repair_budget_exhausted",
        )
        self.assertTrue(rp.check_trace(trace).passed)

    def test_illegal_restart_is_rejected(self):
        trace = rp.RuntimeTrace(
            states=[
                "Intake", "Fact Extraction", "JD Requirement Mapping", "Content Selection",
                "Draft", "Claims", "Deterministic Validation", "Targeted Repair",
                "Fact Extraction",
            ],
            draft_count=1, validator_runs=1, repair_rounds=1,
            remaining_errors=1, remaining_warnings=0, runtime_pass=False,
        )
        result = rp.check_trace(trace)
        self.assertFalse(result.passed)
        self.assertTrue(any("非法回跳" in problem for problem in result.problems))


if __name__ == "__main__":
    unittest.main()

