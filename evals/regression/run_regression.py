#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""optimize-resume 回归 eval runner（Regression Eval）。

Regression Eval 只回答一个问题：

    这次修改 Skill 之后，有没有把以前已经正确的行为搞坏？

它守住的是下限（Safety / Fact Fidelity / Scope / Boundary / 已知失败案例），
目标是通过率接近 100%。它 **不** 判断「新版本是否生成了更好的简历」——
那是 `evals/quality/` 的职责。

包含三个 suite：

    cases     高风险行为的回归测试（evals/regression/cases/*.yaml）
    fixtures  端到端 smoke test（examples/*/fact-store.yaml + resume.md）
    schema    JSON Schema 自检（需要 jsonschema，缺失则跳过）

case 的 `category` 字段把回归用例分为两类，分别统计：

    safety            事实安全类硬门禁（虚构指标、denied 复活、scope 升级……）
    content_quality   确定性的内容质量约束（JD 覆盖、ATS、salience、重复……）

这两类都必须是确定性 pass/fail，不能合成一个综合分；版本优劣判断见 quality eval。

用法:
    python3 evals/regression/run_regression.py
    python3 evals/regression/run_regression.py --suite cases
    python3 evals/regression/run_regression.py --category safety
    python3 evals/regression/run_regression.py --case 001
    python3 evals/regression/run_regression.py --json
    EVAL_JUDGE_CMD="python3 my_judge.py" python3 evals/regression/run_regression.py

judge 扩展接口
--------------
语义类指标（interview_defensibility / relevant_evidence_recall /
irrelevant_content_rate）无法用代码判定。case 里以 `judge_checks` 声明，
runner 在设置环境变量 EVAL_JUDGE_CMD 时把 JSON 从 stdin 传入该命令，
要求命令返回 {"passed": bool, "reason": str}。未设置时如实报告 SKIPPED，
不伪造评分。
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

EVALS_DIR = Path(__file__).resolve().parent.parent
ROOT = EVALS_DIR.parent
SCRIPTS = ROOT / "scripts"
CASES_DIR = EVALS_DIR / "regression" / "cases"
EXAMPLES_DIR = ROOT / "examples"
SCHEMA_PATH = ROOT / "schemas" / "facts.schema.json"

CASE_CATEGORIES = ("safety", "content_quality")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_claims as vc  # noqa: E402
import runtime_protocol as rp  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover
    jsonschema = None

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"

if not sys.stdout.isatty():  # pragma: no cover
    GREEN = RED = YELLOW = DIM = RESET = ""


def color(text: str, tone: str) -> str:
    return f"{tone}{text}{RESET}"


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_store(case_store: Dict[str, Any], variant: Dict[str, Any]) -> Dict[str, Any]:
    store = dict(case_store or {})
    base_claims = list(store.get("claims") or [])
    store["claims"] = base_claims + list(variant.get("claims") or [])
    return store


def issue_codes(issues: Sequence[vc.Issue]) -> List[str]:
    return [issue.code for issue in issues]


def errors_of(issues: Sequence[vc.Issue]) -> List[vc.Issue]:
    return [issue for issue in issues if issue.severity == "error"]


# --------------------------------------------------------------------------
# cases suite
# --------------------------------------------------------------------------

class VariantResult:
    def __init__(self, case_id: str, name: str, expect: str, category: str = "safety") -> None:
        self.case_id = case_id
        self.name = name
        self.expect = expect
        self.category = category
        self.passed = True
        self.problems: List[str] = []
        self.issues: List[vc.Issue] = []
        self.metrics: List[str] = []


def run_variant(case: Dict[str, Any], variant: Dict[str, Any]) -> VariantResult:
    case_id = str(case.get("id"))
    expect = str(variant.get("expect") or "pass")
    category = str(case.get("category") or "safety")
    result = VariantResult(case_id, str(variant.get("name") or "default"), expect, category)
    result.metrics = [str(item) for item in (case.get("metrics") or [])]

    store = build_store(case.get("fact_store") or {}, variant)
    resume = variant.get("resume")
    if resume is None:
        resume = case.get("resume")
    issues = vc.validate(store, resume)
    result.issues = issues
    produced = set(issue_codes(issues))

    if expect == "fail":
        expected = [str(item) for item in (variant.get("expect_issues") or [])]
        missing = [code for code in expected if code not in produced]
        if missing:
            result.passed = False
            result.problems.append(
                "期望检出 " + ", ".join(expected) + "，实际缺少 " + ", ".join(missing))
    else:
        found = [issue for issue in issues if issue.severity == "error"]
        if found:
            result.passed = False
            result.problems.append(f"期望通过，但出现 {len(found)} 个错误")
            for issue in found:
                result.problems.append(f"  ERROR [{issue.code}] {issue.message}")

    for code in variant.get("expect_warnings") or []:
        if str(code) not in produced:
            result.passed = False
            result.problems.append(f"期望出现警告 {code}，实际没有")

    for code in variant.get("forbid_issues") or []:
        if str(code) in produced:
            result.passed = False
            result.problems.append(f"不应出现 {code}，实际出现")

    return result


def run_cases(filter_text: Optional[str],
              category: str = "all") -> Tuple[List[Dict[str, Any]], List[VariantResult]]:
    if yaml is None:
        print(color("PyYAML 未安装，无法运行 cases suite", RED))
        return [], []
    case_files = sorted(CASES_DIR.glob("*.yaml")) + sorted(CASES_DIR.glob("*.yml"))
    if filter_text:
        case_files = [path for path in case_files if filter_text in path.name]

    loaded: List[Dict[str, Any]] = []
    results: List[VariantResult] = []
    for path in case_files:
        case = load_yaml(path)
        if not isinstance(case, dict):
            continue
        if category != "all" and str(case.get("category") or "safety") != category:
            continue
        loaded.append(case)
        for variant in case.get("variants") or []:
            results.append(run_variant(case, variant))
    return loaded, results


# --------------------------------------------------------------------------
# fixtures suite
# --------------------------------------------------------------------------

class FixtureResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self.passed = True
        self.problems: List[str] = []
        self.errors = 0
        self.warnings = 0
        self.lint_errors = 0
        self.lint_warnings = 0


def run_fixture(directory: Path) -> FixtureResult:
    result = FixtureResult(directory.name)
    case_path = directory / "case.yaml"
    store_path = directory / "fact-store.yaml"
    resume_path = directory / "resume.md"

    for required in (case_path, store_path, resume_path):
        if not required.is_file():
            result.passed = False
            result.problems.append(f"缺少 fixture 文件：{required.name}")
            return result

    case = load_yaml(case_path) or {}
    store = vc.load_store(store_path)
    resume_text = read_text(resume_path)

    issues = vc.validate(store, resume_text)
    result.errors = len(errors_of(issues))
    result.warnings = len(issues) - result.errors

    if str(case.get("expect") or "pass") == "pass" and result.errors:
        result.passed = False
        for issue in errors_of(issues):
            result.problems.append(f"  ERROR [{issue.code}] {issue.message}")

    produced = set(issue_codes(issues))
    for code in case.get("expect_warnings") or []:
        if str(code) not in produced:
            result.passed = False
            result.problems.append(f"期望出现警告 {code}，实际没有")
    for code in case.get("forbid_issues") or []:
        if str(code) in produced:
            result.passed = False
            result.problems.append(f"不应出现 {code}，实际出现")

    lint = subprocess.run(
        [sys.executable, str(SCRIPTS / "lint_resume.py"), str(resume_path)],
        capture_output=True, text=True)
    lint_out = lint.stdout.strip()
    for line in lint_out.splitlines():
        if line.startswith("ERROR:"):
            result.lint_errors += 1
        elif line.startswith("WARN:"):
            result.lint_warnings += 1
    if lint.returncode != 0:
        result.passed = False
        result.problems.append("lint_resume.py 未通过：")
        for line in lint_out.splitlines():
            if line.startswith("ERROR:"):
                result.problems.append(f"  {line}")
    return result


def run_fixtures(filter_text: Optional[str]) -> List[FixtureResult]:
    if not EXAMPLES_DIR.is_dir():
        return []
    directories = sorted(p for p in EXAMPLES_DIR.iterdir() if p.is_dir())
    if filter_text:
        directories = [p for p in directories if filter_text in p.name]
    return [run_fixture(path) for path in directories]


# --------------------------------------------------------------------------
# schema suite
# --------------------------------------------------------------------------

def run_schema_check() -> Tuple[str, List[str]]:
    """返回 (status, messages)，status ∈ ok / skipped / failed。"""
    if jsonschema is None:
        return "skipped", ["jsonschema 未安装：pip install jsonschema 后可启用 schema 自检"]
    schema = json.loads(read_text(SCHEMA_PATH))
    targets: List[Path] = []
    if EXAMPLES_DIR.is_dir():
        targets.extend(sorted(EXAMPLES_DIR.glob("*/fact-store.yaml")))
    if CASES_DIR.is_dir():
        targets.extend(sorted(CASES_DIR.glob("*.yaml")))

    problems: List[str] = []
    checked = 0

    def check(instance: Any, label: str) -> None:
        nonlocal checked
        try:
            jsonschema.validate(instance=instance, schema=schema)
            checked += 1
        except jsonschema.ValidationError as exc:  # type: ignore
            location = "/".join(str(part) for part in exc.absolute_path)
            problems.append(f"{label}: {exc.message} @ {location}")

    for path in targets:
        data = load_yaml(path)
        relative = str(path.relative_to(ROOT))
        if not isinstance(data, dict):
            continue
        if "fact_store" in data:
            # eval case：claims 由各 variant 提供，需合成后再校验
            variants = data.get("variants") or [{"name": "default"}]
            for variant in variants:
                store = build_store(data["fact_store"] or {}, variant)
                check(store, f"{relative}::{variant.get('name')}")
        else:
            check(data, relative)

    if problems:
        return "failed", problems
    return "ok", [f"通过 {checked} 份 fact store"]


# --------------------------------------------------------------------------
# runtime protocol suite
# --------------------------------------------------------------------------

def run_runtime_protocol() -> List[rp.ProtocolResult]:
    """检查 Runtime 的状态机、预算和 WARNING 非阻断语义。"""
    return rp.run_builtin_runtime_cases()


# --------------------------------------------------------------------------
# judge 扩展接口
# --------------------------------------------------------------------------

def collect_manual_checks(cases: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Manual review checks 无法自动判定，如实报告为人工待办。"""
    out: List[Dict[str, str]] = []
    for case in cases:
        for check in case.get("manual_checks") or []:
            out.append({
                "case": str(case.get("id")),
                "check": str(check.get("id") or "manual"),
                "metric": str(check.get("metric") or ""),
                "prompt": str(check.get("prompt") or "").strip(),
            })
    return out


def run_judge_checks(cases: Sequence[Dict[str, Any]],
                     variants: Sequence[VariantResult]) -> List[Dict[str, str]]:
    checks: List[Tuple[str, Dict[str, Any]]] = []
    for case in cases:
        for check in case.get("judge_checks") or []:
            checks.append((str(case.get("id")), check))
    if not checks:
        return []

    command = os.environ.get("EVAL_JUDGE_CMD")
    if not command:
        return [{"status": "skipped", "case": cid,
                 "detail": str(check.get("id") or "judge")}
                for cid, check in checks]

    outcomes: List[Dict[str, str]] = []
    for cid, check in checks:
        payload = json.dumps({
            "case_id": cid,
            "check_id": check.get("id"),
            "metric": check.get("metric"),
            "prompt": check.get("prompt"),
            "fact_store": check.get("fact_store"),
            "resume": check.get("resume"),
        }, ensure_ascii=False)
        try:
            proc = subprocess.run(shlex.split(command), input=payload,
                                  capture_output=True, text=True, timeout=180)
            verdict = json.loads(proc.stdout or "{}")
            outcomes.append({
                "status": "passed" if verdict.get("passed") else "failed",
                "case": cid,
                "detail": str(check.get("id") or "judge"),
                "reason": str(verdict.get("reason") or ""),
            })
        except Exception as exc:
            outcomes.append({"status": "failed", "case": cid,
                             "detail": str(check.get("id") or "judge"),
                             "reason": f"judge 调用失败: {exc}"})
    return outcomes


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

DETERMINISTIC_METRICS = set(vc.METRIC_BY_CODE.values())


def metric_table(variants: Sequence[VariantResult]) -> List[Tuple[str, int, int]]:
    """只统计：

    1. expect: pass 的变体——正确场景下指标是否成立；
    2. 有 deterministic issue code 映射的指标——否则不计算，避免伪造评分。
    """
    totals: Dict[str, List[bool]] = {}
    for result in variants:
        if result.expect != "pass" or not result.metrics:
            continue
        failed_metrics = {vc.METRIC_BY_CODE.get(issue.code, "")
                          for issue in result.issues if issue.severity == "error"}
        for metric in result.metrics:
            if metric not in DETERMINISTIC_METRICS:
                continue
            totals.setdefault(metric, []).append(metric not in failed_metrics)
    rows = []
    for metric in sorted(totals):
        values = totals[metric]
        rows.append((metric, sum(1 for value in values if value), len(values)))
    return rows


def non_deterministic_metrics(variants: Sequence[VariantResult]) -> List[str]:
    declared = {metric for result in variants for metric in result.metrics}
    return sorted(declared - DETERMINISTIC_METRICS)


def category_table(variants: Sequence[VariantResult]) -> List[Tuple[str, int, int]]:
    counts: Dict[str, List[bool]] = {}
    for result in variants:
        counts.setdefault(result.category, []).append(result.passed)
    return [(name, sum(1 for v in values if v), len(values))
            for name, values in sorted(counts.items())]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="optimize-resume regression eval")
    parser.add_argument("--suite", choices=["all", "cases", "fixtures", "schema"],
                        default="all")
    parser.add_argument("--case", help="只运行文件名或目录名包含该字符串的 case")
    parser.add_argument("--category", choices=["all"] + list(CASE_CATEGORIES),
                        default="all", help="按 case category 过滤：safety / content_quality")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if yaml is None:
        print("需要 PyYAML：python3 -m pip install pyyaml")
        return 2

    cases: List[Dict[str, Any]] = []
    variant_results: List[VariantResult] = []
    fixture_results: List[FixtureResult] = []
    runtime_results: List[rp.ProtocolResult] = []
    schema_status, schema_messages = "skipped", []
    judge_outcomes: List[Dict[str, str]] = []
    manual_checks: List[Dict[str, str]] = []

    if args.suite in ("all", "cases"):
        cases, variant_results = run_cases(args.case, args.category)
        judge_outcomes = run_judge_checks(cases, variant_results)
        manual_checks = collect_manual_checks(cases)
    if args.suite in ("all", "fixtures"):
        fixture_results = run_fixtures(args.case)
    if args.suite in ("all", "schema"):
        schema_status, schema_messages = run_schema_check()
    if args.suite in ("all", "cases"):
        runtime_results = run_runtime_protocol()

    failed_cases = [r for r in variant_results if not r.passed]
    failed_fixtures = [r for r in fixture_results if not r.passed]
    failed_runtime = [r for r in runtime_results if not r.passed]
    failed_judge = [o for o in judge_outcomes if o["status"] == "failed"]

    if args.json:
        print(json.dumps({
            "cases": {
                "total": len(cases),
                "variants": len(variant_results),
                "by_category": [{"category": c, "passed": p, "total": t}
                                for c, p, t in category_table(variant_results)],
                "failed_variants": [{"case": r.case_id, "variant": r.name,
                                     "category": r.category,
                                     "problems": r.problems} for r in failed_cases],
            },
            "fixtures": [{"name": r.name, "passed": r.passed,
                          "errors": r.errors, "warnings": r.warnings,
                          "lint_errors": r.lint_errors, "lint_warnings": r.lint_warnings,
                          "problems": r.problems} for r in fixture_results],
            "runtime_protocol": {
                "total": len(runtime_results),
                "passed": len(runtime_results) - len(failed_runtime),
                "failed_cases": [
                    {"case": r.case_id, "problems": r.problems}
                    for r in failed_runtime
                ],
            },
            "schema": {"status": schema_status, "messages": schema_messages},
            "metrics": [{"metric": m, "passed": p, "total": t}
                        for m, p, t in metric_table(variant_results)],
            "judge": judge_outcomes,
            "manual": manual_checks,
        }, ensure_ascii=False, indent=2))
        return 1 if (failed_cases or failed_fixtures or failed_runtime or failed_judge
                     or schema_status == "failed") else 0

    print("=" * 68)
    print("optimize-resume regression eval")
    print("=" * 68)

    if args.suite in ("all", "cases"):
        print("\n[cases]")
        by_case: Dict[str, List[VariantResult]] = {}
        for result in variant_results:
            by_case.setdefault(result.case_id, []).append(result)
        for category, passed, total in category_table(variant_results):
            tone = GREEN if passed == total else RED
            print(f"  {color(category, tone)}: {passed}/{total} variants passed")
        print()
        for case_id in sorted(by_case):
            results = by_case[case_id]
            bad = [r for r in results if not r.passed]
            mark = color("PASS", GREEN) if not bad else color("FAIL", RED)
            category = results[0].category if results else "?"
            print(f"  {mark} {case_id:<42} [{category}] "
                  f"{len(results) - len(bad)}/{len(results)} variants")
            for result in bad:
                print(f"        {color('variant', DIM)} {result.name} (expect {result.expect})")
                for problem in result.problems:
                    print(f"          {problem}")

        rows = metric_table(variant_results)
        if rows:
            print("\n[metrics]  (仅统计 expect: pass 的变体；仅 deterministic 指标)")
            for metric, passed, total in rows:
                rate = f"{passed / total * 100:.0f}%" if total else "-"
                tone = GREEN if passed == total else RED
                print(f"  {metric:<32} {passed}/{total:<4} {color(rate, tone)}")
            semantic = non_deterministic_metrics(variant_results)
            if semantic:
                print(f"  {color('不需要自动评分', DIM)}：{', '.join(semantic)}"
                      " → 见 [judge] / [manual]")

        if judge_outcomes:
            print("\n[judge]")
            skipped = [o for o in judge_outcomes if o["status"] == "skipped"]
            if skipped:
                print(f"  {color('SKIP', YELLOW)} {len(skipped)} 项语义 check 未执行"
                      "（未设置 EVAL_JUDGE_CMD，不伪造评分）")
                for outcome in skipped:
                    print(f"        {outcome['case']} / {outcome['detail']}")
            for outcome in judge_outcomes:
                if outcome["status"] == "skipped":
                    continue
                tone = GREEN if outcome["status"] == "passed" else RED
                print(f"  {color(outcome['status'].upper(), tone)} "
                      f"{outcome['case']} / {outcome['detail']} {outcome.get('reason', '')}")

        if manual_checks:
            print("\n[manual]")
            print(f"  {color('TODO', YELLOW)} {len(manual_checks)} 项需要人工复核（不自动评分）")
            for check in manual_checks:
                metric = f" ({check['metric']})" if check["metric"] else ""
                print(f"        {check['case']} / {check['check']}{metric}")

        print("\n[runtime protocol]")
        for result in runtime_results:
            mark = color("PASS", GREEN) if result.passed else color("FAIL", RED)
            print(f"  {mark} {result.case_id}")
            for problem in result.problems:
                print(f"        {problem}")

    if args.suite in ("all", "fixtures"):
        print("\n[fixtures]  (端到端 smoke test)")
        if not fixture_results:
            print(f"  {color('SKIP', YELLOW)} 没有找到 examples/*/fixture")
        for result in fixture_results:
            mark = color("PASS", GREEN) if result.passed else color("FAIL", RED)
            print(f"  {mark} {result.name:<32} validate={result.errors}E/{result.warnings}W"
                  f"  lint={result.lint_errors}E/{result.lint_warnings}W")
            for problem in result.problems:
                print(f"        {problem}")

    if args.suite in ("all", "schema"):
        print("\n[schema]")
        tone = {"ok": GREEN, "skipped": YELLOW, "failed": RED}[schema_status]
        print(f"  {color(schema_status.upper(), tone)}")
        for message in schema_messages:
            print(f"        {message}")

    print("\n" + "-" * 68)
    print(f"summary: cases {len(variant_results) - len(failed_cases)} passed, "
          f"{len(failed_cases)} failed | "
          f"runtime {len(runtime_results) - len(failed_runtime)} passed, "
          f"{len(failed_runtime)} failed | "
          f"fixtures {len(fixture_results) - len(failed_fixtures)} passed, "
          f"{len(failed_fixtures)} failed | "
          f"schema {schema_status}")
    return 1 if (failed_cases or failed_fixtures or failed_runtime or failed_judge
                 or schema_status == "failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
