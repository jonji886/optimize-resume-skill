#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Runtime execution protocol checks.

The resume Skill is orchestrated by an agent, so its full execution cannot be
unit-tested without pretending to be an LLM.  This module instead validates a
small, serializable runtime trace: the bounded state transitions, repair
budget, and pass semantics that the Skill promises to follow.

It deliberately does not score wording quality and does not run Quality Eval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence


MAX_REPAIR_ROUNDS = 2
MAX_VALIDATOR_RUNS = 3
MAX_DRAFTS = 1
MAX_ATS_PASSES = 1
MAX_SALIENCE_PASSES = 1
MAX_LINT_RUNS = 1

STATE_ORDER = (
    "Intake",
    "Fact Extraction",
    "JD Requirement Mapping",
    "Content Selection",
    "Draft",
    "Claims",
    "Deterministic Validation",
    "Targeted Repair",
    "ATS",
    "Recruiter Salience",
    "Lint",
    "Deliver",
)
_STATE_INDEX = {name: index for index, name in enumerate(STATE_ORDER)}


@dataclass
class RuntimeTrace:
    """Observable counters from one normal resume-optimization run."""

    states: Sequence[str]
    draft_count: int
    validator_runs: int
    repair_rounds: int
    remaining_errors: int
    remaining_warnings: int
    runtime_pass: bool
    lint_errors: int = 0
    warning_reviews: int = 0
    local_repairs: int = 0
    full_rewrite_count: int = 0
    terminated: bool = True
    termination_reason: str = ""


@dataclass
class ProtocolResult:
    case_id: str
    passed: bool = True
    problems: List[str] = field(default_factory=list)


def _count(states: Iterable[str], target: str) -> int:
    return sum(1 for state in states if state == target)


def _check_state_transitions(states: Sequence[str]) -> List[str]:
    problems: List[str] = []
    if not states:
        return ["状态轨迹为空"]

    for state in states:
        if state not in _STATE_INDEX:
            problems.append(f"未知状态：{state}")

    if problems:
        return problems

    # The only permitted backward transition is Validation -> Targeted Repair
    # -> Validation.  No later state may reopen fact/JD/selection work.
    previous = -1
    for index, state in enumerate(states):
        current = _STATE_INDEX[state]
        if current >= previous:
            previous = current
            continue
        allowed_repair_backtrack = (
            state == "Deterministic Validation"
            and index > 0
            and states[index - 1] == "Targeted Repair"
        )
        if not allowed_repair_backtrack:
            problems.append(
                f"非法回跳：{states[index - 1]} -> {state}；"
                "没有新 evidence 不得重开前序阶段"
            )
        previous = current

    return problems


def check_trace(trace: RuntimeTrace, case_id: str = "runtime") -> ProtocolResult:
    result = ProtocolResult(case_id=case_id)
    result.problems.extend(_check_state_transitions(trace.states))

    counters = (
        ("draft_count", trace.draft_count, 1, MAX_DRAFTS),
        ("validator_runs", trace.validator_runs, 1, MAX_VALIDATOR_RUNS),
        ("repair_rounds", trace.repair_rounds, 0, MAX_REPAIR_ROUNDS),
        ("ATS passes", _count(trace.states, "ATS"), 0, MAX_ATS_PASSES),
        ("Recruiter Salience passes", _count(trace.states, "Recruiter Salience"), 0,
         MAX_SALIENCE_PASSES),
        ("lint runs", _count(trace.states, "Lint"), 0, MAX_LINT_RUNS),
    )
    for name, value, lower, upper in counters:
        if value < lower or value > upper:
            result.problems.append(f"{name}={value} 超出预算 [{lower}, {upper}]")

    if _count(trace.states, "Draft") != trace.draft_count:
        result.problems.append("Draft 状态次数必须与 draft_count 一致")
    if _count(trace.states, "Deterministic Validation") != trace.validator_runs:
        result.problems.append("Validation 状态次数必须与 validator_runs 一致")
    if _count(trace.states, "Targeted Repair") != trace.repair_rounds:
        result.problems.append("Targeted Repair 状态次数必须与 repair_rounds 一致")

    for name, value in (("remaining_errors", trace.remaining_errors),
                        ("remaining_warnings", trace.remaining_warnings),
                        ("lint_errors", trace.lint_errors),
                        ("warning_reviews", trace.warning_reviews),
                        ("local_repairs", trace.local_repairs),
                        ("full_rewrite_count", trace.full_rewrite_count)):
        if value < 0:
            result.problems.append(f"{name} 不得为负数：{value}")

    # Warnings are observations, not the success function.  Lint errors are
    # blocking at delivery, just like validator errors.
    expected_pass = trace.remaining_errors == 0 and trace.lint_errors == 0
    if trace.runtime_pass != expected_pass:
        result.problems.append(
            f"runtime_pass={trace.runtime_pass} 与 blocking 结果不一致；"
            "remaining_warnings 不参与 PASS 判定"
        )

    if trace.validator_runs > 0 and trace.repair_rounds > trace.validator_runs - 1:
        result.problems.append("repair_rounds 不得多于 validator_runs - 1")

    if trace.full_rewrite_count > 0:
        result.problems.append("Targeted Repair 不得触发全量 rewrite")

    if trace.remaining_errors == 0 and trace.lint_errors == 0:
        required_states = (
            "Intake", "Fact Extraction", "JD Requirement Mapping", "Content Selection",
            "Draft", "Claims", "Deterministic Validation", "ATS",
            "Recruiter Salience", "Lint", "Deliver",
        )
        missing = [state for state in required_states if state not in trace.states]
        if missing:
            result.problems.append("成功交付缺少状态：" + ", ".join(missing))

    if trace.repair_rounds == MAX_REPAIR_ROUNDS and trace.remaining_errors > 0:
        if not trace.terminated or trace.termination_reason != "repair_budget_exhausted":
            result.problems.append(
                "达到 repair budget 仍有 ERROR 时必须停止并报告 unresolved ERROR"
            )

    result.passed = not result.problems
    return result


def _base_states(*, repairs: int = 0, include_delivery: bool = True) -> List[str]:
    states = list(STATE_ORDER[:7])
    for _ in range(repairs):
        states.extend(["Targeted Repair", "Deterministic Validation"])
    if include_delivery:
        states.extend(["ATS", "Recruiter Salience", "Lint", "Deliver"])
    return states


def builtin_runtime_cases() -> Dict[str, RuntimeTrace]:
    """Representative protocol cases required by the Skill contract."""

    return {
        "support-overreach-repaired": RuntimeTrace(
            states=_base_states(repairs=1), draft_count=1, validator_runs=2,
            repair_rounds=1, remaining_errors=0, remaining_warnings=0,
            runtime_pass=True, warning_reviews=0, local_repairs=1,
        ),
        "reasonable-support-wording": RuntimeTrace(
            states=_base_states(), draft_count=1, validator_runs=1,
            repair_rounds=0, remaining_errors=0, remaining_warnings=0,
            runtime_pass=True,
        ),
        "duplicate-warning-local-review": RuntimeTrace(
            states=_base_states(), draft_count=1, validator_runs=1,
            repair_rounds=0, remaining_errors=0, remaining_warnings=1,
            runtime_pass=True, warning_reviews=1,
        ),
        "warning-remains-pass": RuntimeTrace(
            states=_base_states(), draft_count=1, validator_runs=1,
            repair_rounds=0, remaining_errors=0, remaining_warnings=2,
            runtime_pass=True, warning_reviews=1,
        ),
        "repair-budget-exhausted": RuntimeTrace(
            states=_base_states(repairs=2, include_delivery=False), draft_count=1,
            validator_runs=3, repair_rounds=2, remaining_errors=1,
            remaining_warnings=0, runtime_pass=False, terminated=True,
            termination_reason="repair_budget_exhausted", local_repairs=2,
        ),
    }


def run_builtin_runtime_cases() -> List[ProtocolResult]:
    return [check_trace(trace, case_id) for case_id, trace in builtin_runtime_cases().items()]
