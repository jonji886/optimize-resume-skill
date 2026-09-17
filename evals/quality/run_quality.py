#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality / Capability Benchmark runner。

它回答的问题与 Regression Eval 完全不同：

    Regression Eval  这一次修改有没有把以前已经正确的行为搞坏？
    Quality Eval     两个版本都没有明显事实错误时，哪个版本生成的简历更有效？

流水线（每个 case）：

    baseline 产物 ─┐
                   ├─→ Fact Gate（Hard Gate，deterministic + 可选语义层）
    candidate 产物 ─┘        │
                             ├─ 任一方 FAIL → 直接判定，不做质量比较
                             └─ 双方 PASS   → Blind Pairwise Judge × 2（A/B 互换）
                                                  │
                                                  └─ 映射回真实版本 → Position Consistency
                             ↓
                     Evidence Recall / 报告聚合

用法：

    # 无 API Key 也能跑：验证 Harness（生成 mock 产物 + mock judge）
    python3 evals/quality/run_quality.py --mock

    # 只验证加载、路径、Judge Input、A/B 随机化，不调用 Judge
    python3 evals/quality/run_quality.py --dry-run

    # 单个 case
    python3 evals/quality/run_quality.py --case ai-fde-001 --dry-run

    # 真实 Judge（需要 API Key；provider / model 可用环境变量或参数指定）
    QUALITY_JUDGE_PROVIDER=openai QUALITY_JUDGE_MODEL=gpt-4o \\
      python3 evals/quality/run_quality.py --baseline v0.6 --candidate v0.7

    # 用已保存的两套产物做比较（不重新生成）
    python3 evals/quality/run_quality.py --runs-dir evals/quality/runs \\
      --baseline v0.6 --candidate v0.7

产物目录约定（`--runs-dir`）：

    <runs-dir>/<case-id>/baseline.md
    <runs-dir>/<case-id>/baseline.facts.yaml   （可选但强烈建议：缺失会导致 Fact Gate 退化）
    <runs-dir>/<case-id>/candidate.md
    <runs-dir>/<case-id>/candidate.facts.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
EVALS_DIR = HERE.parent
REPO_ROOT = EVALS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.common import judge_client as jc  # noqa: E402
from evals.common import loaders as ld  # noqa: E402
from evals.common import report as rp  # noqa: E402
from evals.common import schemas as sc  # noqa: E402
from evals.common.loaders import BenchmarkCase, LoadError, Prompt, RunOutput  # noqa: E402
from evals.quality import evidence as ev  # noqa: E402
from evals.quality import fact_gate as fg  # noqa: E402
from evals.quality import mock_generator as mg  # noqa: E402
from evals.quality import pairwise as pw  # noqa: E402

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"
if not sys.stdout.isatty():  # pragma: no cover
    GREEN = RED = YELLOW = DIM = BOLD = RESET = ""


def color(text: str, tone: str) -> str:
    return f"{tone}{text}{RESET}"


# --------------------------------------------------------------------------
# 产物获取
# --------------------------------------------------------------------------

def outputs_from_mock(case: BenchmarkCase, mock_mode: str,
                      runs_dir: Optional[Path]) -> Tuple[RunOutput, RunOutput, List[str]]:
    store = case.fact_store()
    baseline, candidate = mg.generate_pair(
        case.case_id, store, case.capabilities(), mock_mode=mock_mode)
    notes = list(baseline.notes) + list(candidate.notes)
    if runs_dir is not None:
        # 同时落盘，方便人工查看 mock 产物差异、复现 Judge 输入，
        # 也便于用 --runs-dir 复用同一批产物做对照实验。
        ld.write_run_output(case.case_id, "baseline", baseline.resume_text,
                            baseline.facts, runs_dir=runs_dir)
        ld.write_run_output(case.case_id, "candidate", candidate.resume_text,
                            candidate.facts, runs_dir=runs_dir)
    baseline_output = RunOutput(label="baseline", resume_path=None, facts_path=None,
                                resume_text=baseline.resume_text, facts=baseline.facts)
    candidate_output = RunOutput(label="candidate", resume_path=None, facts_path=None,
                                 resume_text=candidate.resume_text, facts=candidate.facts)
    return baseline_output, candidate_output, notes


def outputs_from_runs(case: BenchmarkCase,
                      runs_dir: Path) -> Optional[Tuple[RunOutput, RunOutput, List[str]]]:
    loaded = ld.load_run_outputs(runs_dir, case.case_id)
    if loaded is None:
        return None
    baseline, candidate = loaded
    notes: List[str] = []
    for output in (baseline, candidate):
        if not output.has_facts:
            notes.append(
                f"{output.label} 未提供 facts sidecar，Fact Gate 与 Evidence Recall 退化。")
    return baseline, candidate, notes


# --------------------------------------------------------------------------
# Fact Gate
# --------------------------------------------------------------------------

def gate_for(case: BenchmarkCase, output: RunOutput, strict: bool) -> fg.GateResult:
    return fg.evaluate(
        store=output.facts,
        resume_text=output.resume_text,
        ground_truth=case.fact_store(),
        forbidden_claims=case.forbidden_claims(),
        strict=strict)


def apply_semantic_guard(result: fg.GateResult, case: BenchmarkCase, output: RunOutput,
                         client: jc.JudgeClient, prompt: Prompt) -> fg.GateResult:
    user, payload = fg.build_fact_guard_input(
        case.fact_store(), case.source_resume_text(), output.resume_text)
    try:
        raw = client.complete(prompt.body, user, payload)
    except jc.JudgeError as exc:
        result.notes.append(f"语义 fact guard 未执行（{exc}），本 case 仅用 deterministic Gate。")
        return result
    data = sc.extract_json_object(raw or "")
    if data is None:
        result.notes.append("语义 fact guard 返回内容无法解析为 JSON，已忽略。")
        return result
    return fg.apply_semantic_result(result, data, model=prompt.fingerprint())


def run_evidence_judge(case: BenchmarkCase, output: RunOutput, client: jc.JudgeClient,
                       prompt: Prompt) -> Dict[str, Any]:
    """Judge 2：JD Evidence Judge（逐份诊断，参与分数计算但不参与胜负判定）。

    deterministic 版本的 Evidence Recall 见 `evidence.py`；这里是 LLM 版本，
    用来交叉验证「哪些高价值 evidence 没被用上」，结果只写进报告。
    """
    from evals.quality.render import render_evidence_input

    user = render_evidence_input(
        case.jd_text(), case.fact_store(), output.resume_text, case.context_text())
    payload = {"case_id": case.case_id, "resume": output.resume_text,
               "jd": case.jd_text(), "ground_truth": case.fact_store()}
    try:
        raw = client.complete(prompt.body, user, payload)
    except jc.JudgeError as exc:
        return {"error": str(exc), "prompt": prompt.fingerprint()}
    data = sc.extract_json_object(raw or "")
    if data is None:
        return {"error": "输出无法解析为 JSON", "prompt": prompt.fingerprint()}
    return {
        "prompt": prompt.fingerprint(),
        "evidence_recall": data.get("evidence_recall"),
        "available_high_value_evidence": data.get("available_high_value_evidence"),
        "used_high_value_evidence": data.get("used_high_value_evidence"),
        "missed_high_value_evidence": data.get("missed_high_value_evidence") or [],
        "requirement_count": len(data.get("requirements") or []),
    }


# --------------------------------------------------------------------------
# 单 case 执行
# --------------------------------------------------------------------------

def run_case(case: BenchmarkCase, baseline: RunOutput, candidate: RunOutput,
             *, mode: str, client: Optional[jc.JudgeClient], prompts: Dict[str, Prompt],
             strict_gate: bool, semantic_guard: bool, evidence_judge: bool, seed: int,
             input_notes: Sequence[str] = ()) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "case_id": case.case_id,
        "role_family": case.role_family,
        "difficulty": case.difficulty,
        "source": case.source,
        "mode": mode,
        "notes": list(input_notes),
        "decided_by": None,
        "position_consistency": None,
        "rounds": [],
        "dimensions": {},
        "overall": {},
        "human_review": case.human_review or None,
    }

    # --- Fact Gate（Hard Gate） -------------------------------------------
    baseline_gate = gate_for(case, baseline, strict_gate)
    candidate_gate = gate_for(case, candidate, strict_gate)
    if semantic_guard and client is not None and mode != "dry-run":
        baseline_gate = apply_semantic_guard(baseline_gate, case, baseline,
                                            client, prompts["fact_guard"])
        candidate_gate = apply_semantic_guard(candidate_gate, case, candidate,
                                             client, prompts["fact_guard"])

    result["fact_gate"] = {"baseline": baseline_gate.to_dict(),
                           "candidate": candidate_gate.to_dict()}

    # --- Evidence Recall（诊断指标，不参与胜负判定） -----------------------
    capabilities = case.capabilities()
    result["evidence_recall"] = {
        side: ev.compute_evidence_recall(
            case.fact_store(), output.resume_text, capabilities,
            must_preserve=case.must_preserve(), run_store=output.facts).to_dict()
        for side, output in (("baseline", baseline), ("candidate", candidate))
    }

    # Judge 2（可选）：LLM 版 JD Evidence 诊断，只写报告，不参与胜负判定。
    if evidence_judge and client is not None and prompts.get("evidence_judge"):
        result["evidence_judge"] = {
            side: run_evidence_judge(case, output, client, prompts["evidence_judge"])
            for side, output in (("baseline", baseline), ("candidate", candidate))
        }

    if mode == "dry-run":
        result["outcome"] = rp.OUTCOME_DRY_RUN
        result["decided_by"] = "dry_run"
        return result

    # --- Hard Gate 否决 ----------------------------------------------------
    if not baseline_gate.passed and not candidate_gate.passed:
        result["outcome"] = rp.OUTCOME_BOTH_FACT_FAIL
        result["decided_by"] = "fact_gate"
        result["notes"].append("双方都未通过 Fact Gate，该 case 判为 INVALID / BOTH_FAIL。")
        return result
    if not candidate_gate.passed:
        result["outcome"] = rp.OUTCOME_CANDIDATE_FACT_FAIL
        result["decided_by"] = "fact_gate"
        result["notes"].append("candidate 出现事实安全违规，事实错误不能被表达质量抵消。")
        return result
    if not baseline_gate.passed:
        result["outcome"] = rp.OUTCOME_BASELINE_FACT_FAIL
        result["decided_by"] = "fact_gate"
        result["notes"].append("baseline 出现事实安全违规，由 Fact Gate 直接判定。")
        return result

    # --- Blind Pairwise + Position Swap -----------------------------------
    assert client is not None and prompts.get("pairwise_judge") is not None
    outcome = pw.run_pairwise(
        case_id=case.case_id,
        texts={"baseline": baseline.resume_text, "candidate": candidate.resume_text},
        client=client,
        system_prompt=prompts["pairwise_judge"].body,
        jd_text=case.jd_text(),
        ground_truth=case.fact_store(),
        source_resume=case.source_resume_text(),
        context=case.context_text(),
        capabilities=capabilities,
        ats_keywords=case.jd_ats_keywords(),
        seed=seed)

    result["rounds"] = [r.to_dict() for r in outcome.rounds]
    result["position_consistency"] = outcome.position_consistent
    result["dimensions"] = outcome.dimensions
    result["overall"] = outcome.overall
    result["decided_by"] = "judge"

    if outcome.error:
        result["outcome"] = rp.OUTCOME_JUDGE_ERROR
        result["judge_error"] = outcome.error
        result["notes"].append(f"judge 调用/解析失败：{outcome.error}")
        return result
    if outcome.position_consistent is False:
        result["outcome"] = rp.OUTCOME_POSITION_INCONSISTENT
        return result
    if outcome.winner == pw.LABEL_CANDIDATE:
        result["outcome"] = rp.OUTCOME_CANDIDATE_WIN
    elif outcome.winner == pw.LABEL_BASELINE:
        result["outcome"] = rp.OUTCOME_BASELINE_WIN
    else:
        result["outcome"] = rp.OUTCOME_TIE
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="optimize-resume quality benchmark（版本对比）")
    parser.add_argument("--baseline", default="baseline", help="baseline 版本标签（只影响报告）")
    parser.add_argument("--candidate", default="candidate", help="candidate 版本标签")
    parser.add_argument("--case", action="append", default=[],
                        help="只跑指定 case id，可重复传入")
    parser.add_argument("--role-family", help="只跑指定 role family")
    parser.add_argument("--runs-dir", help="baseline / candidate 产物目录")
    parser.add_argument("--reports-dir", default=str(ld.REPORTS_DIR),
                        help="报告输出目录，默认 evals/reports")

    parser.add_argument("--dry-run", action="store_true",
                        help="不调用 Judge，只验证 benchmark 加载、路径、Judge Input 与 A/B 随机化")
    parser.add_argument("--mock", action="store_true",
                        help="用确定性启发式 judge + mock 产物跑通整条 Harness（CI 用）")
    parser.add_argument("--mock-mode",
                        choices=["candidate-wins", "equal", "baseline-wins"],
                        default="candidate-wins",
                        help="mock 场景的已知答案，用于自检 Harness 是否能判对方向")

    parser.add_argument("--judge-provider", choices=list(jc.PROVIDERS))
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-cmd", help="subprocess judge 命令")
    parser.add_argument("--judge-endpoint")
    parser.add_argument("--judge-temperature", type=float)
    parser.add_argument("--judge-max-tokens", type=int)
    parser.add_argument("--semantic-fact-guard", action="store_true",
                        help="额外用 LLM 做语义层事实安全检查（只能增加否决项）")
    parser.add_argument("--evidence-judge", action="store_true",
                        help="额外用 LLM 做 JD Evidence 诊断（只写报告，不参与胜负判定）")
    parser.add_argument("--strict-gate", action="store_true",
                        help="把 scope / 商业交付的 warning 级问题也升级为 Gate 否决项")
    parser.add_argument("--seed", type=int, default=0,
                        help="A/B 位置随机的 seed，写入报告以保证可复现")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--fail-on-regression", action="store_true",
                        help="存在 candidate 相对 baseline 退化的 case 时返回非零退出码")
    parser.add_argument("--skill-version", help="被测 Skill 版本，仅写入报告")
    return parser


def validate_benchmark(cases: Sequence[BenchmarkCase]) -> List[str]:
    problems: List[str] = []
    for case in cases:
        problems.extend(sc.validate_benchmark_case(case))
    return problems


def resolve_mode(args) -> str:
    if args.dry_run:
        return "dry-run"
    if args.mock:
        return "mock"
    return "real"


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    # ---- 加载与校验 benchmark ------------------------------------------
    only = [item for chunk in args.case for item in str(chunk).split(",") if item]
    try:
        cases = ld.discover_benchmark_cases(only=only or None,
                                            role_family=args.role_family)
    except LoadError as exc:
        print(color(f"错误: {exc}", RED))
        return 2

    problems = validate_benchmark(cases)
    if problems:
        print(color("benchmark case 校验失败：", RED))
        for problem in problems:
            print(f"  - {problem}")
        return 2
    if not cases:
        print(color("没有匹配的 benchmark case。", RED))
        return 2

    mode = resolve_mode(args)
    runs_dir = Path(args.runs_dir).expanduser().resolve() if args.runs_dir else None
    if mode == "mock" and runs_dir is None:
        runs_dir = ld.RUNS_DIR  # 便于人工对比 mock 产物，同时验证落盘路径

    # 语义层 fact guard 需要真实模型：mock judge 没有语义判断能力，
    # 让它冒充语义检查会制造虚假的安全感，所以直接忽略并说明。
    semantic_guard = bool(args.semantic_fact_guard) and mode == "real"
    evidence_judge = bool(args.evidence_judge) and mode == "real"
    if (args.semantic_fact_guard or args.evidence_judge) and mode != "real":
        print(color("注意：--semantic-fact-guard / --evidence-judge 只在真实 judge 模式下生效，已忽略。",
                    YELLOW))

    # ---- Judge client ---------------------------------------------------
    prompts: Dict[str, Prompt] = {}
    client: Optional[jc.JudgeClient] = None
    judge_config = None
    if mode != "dry-run":
        try:
            prompts["pairwise_judge"] = ld.load_prompt("pairwise_judge")
            prompts["fact_guard"] = ld.load_prompt("fact_guard")
            prompts["evidence_judge"] = ld.load_prompt("evidence_judge")
        except LoadError as exc:
            print(color(f"错误: {exc}", RED))
            return 2
        overrides: Dict[str, Any] = {
            "provider": "mock" if mode == "mock" else args.judge_provider,
            "model": args.judge_model,
            "command": args.judge_cmd,
            "endpoint": args.judge_endpoint,
            "temperature": args.judge_temperature,
            "max_tokens": args.judge_max_tokens,
        }
        try:
            judge_config = jc.judge_config_from_env(overrides)
            client = jc.build_judge_client(judge_config)
        except jc.JudgeError as exc:
            print(color(f"错误: {exc}", RED))
            print("提示：没有 API Key 时用 --mock 或 --dry-run 验证 Harness。")
            return 2
        if mode == "real" and judge_config.provider == "mock":
            print(color(
                "注意：未指定真实 judge provider，将使用 mock judge。"
                "mock 是确定性启发式，不是真实质量判断，结果不能作为版本结论。", YELLOW))

    # ---- 产物来源 --------------------------------------------------------
    if mode == "real":
        missing: List[str] = []
        for case in cases:
            if runs_dir is None or ld.load_run_outputs(runs_dir, case.case_id) is None:
                missing.append(case.case_id)
        if missing:
            print(color("错误: 以下 case 缺少可比较产物：", RED))
            for case_id in missing:
                print(f"  - {runs_dir or ld.RUNS_DIR}/{case_id}/"
                      f"{{baseline,candidate}}.md (+ .facts.yaml)")
            print("先保存两套产物，或用 --mock / --dry-run 验证 Harness。")
            return 2

    # ---- 逐 case 执行 ----------------------------------------------------
    results: List[Dict[str, Any]] = []
    dry_run_dump: List[Dict[str, Any]] = []
    for case in cases:
        if mode == "mock" or mode == "dry-run":
            baseline, candidate, input_notes = outputs_from_mock(case, args.mock_mode, runs_dir)
        else:
            loaded = outputs_from_runs(case, runs_dir)
            assert loaded is not None
            baseline, candidate, input_notes = loaded

        if mode == "dry-run":
            dry_run_dump.append(build_dry_run_dump(case, baseline, candidate, args))

        results.append(run_case(
            case, baseline, candidate,
            mode=mode, client=client, prompts=prompts,
            strict_gate=args.strict_gate,
            semantic_guard=semantic_guard,
            evidence_judge=evidence_judge,
            seed=args.seed,
            input_notes=input_notes))

    # ---- 聚合与报告 ------------------------------------------------------
    agg = rp.aggregate(results)
    meta = rp.build_meta({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "baseline_version": args.baseline,
        "candidate_version": args.candidate,
        "skill_version": args.skill_version or "(未指定)",
        "benchmark_version": ld.benchmark_version().get("version", "unknown"),
        "benchmark_cases": len(cases),
        "benchmark_coverage": ld.benchmark_stats(cases),
        "mode": mode,
        "mock_mode": args.mock_mode if mode == "mock" else "",
        "seed": args.seed,
        "position_swap": "enabled（每个 case 两轮镜像 A/B）",
        "fact_gate": "deterministic + semantic" if semantic_guard else "deterministic",
        "strict_gate": args.strict_gate,
        "pairwise_prompt": (prompts["pairwise_judge"].fingerprint()
                            if prompts.get("pairwise_judge") else "(dry-run 未加载)"),
        "fact_guard_prompt": (prompts["fact_guard"].fingerprint()
                              if prompts.get("fact_guard") else "(dry-run 未加载)"),
        "evidence_prompt": (prompts["evidence_judge"].fingerprint()
                            if (evidence_judge and prompts.get("evidence_judge"))
                            else "disabled"),
        "judge_provider": judge_config.provider if judge_config else "none",
        "judge_model": judge_config.model if judge_config else "none",
        "judge_temperature": judge_config.temperature if judge_config else None,
        "judge_max_tokens": judge_config.max_tokens if judge_config else None,
        "judge_endpoint": (judge_config.endpoint or "") if judge_config else "",
        "judge_api_key_present": (bool(judge_config.resolve_api_key())
                                 if judge_config else False),
        "schema_check": sc.json_schema_status(),
    })

    if mode == "dry-run":
        print_dry_run(args, cases, dry_run_dump)
    else:
        print_console(meta, agg, results)

    # dry-run 也落盘：报告里带 mode=dry-run 标注，可用于检查元信息与 schema。
    json_path, md_path = rp.write_reports(Path(args.reports_dir), meta, agg, results)
    print(f"\nreport: {json_path}")
    print(f"report: {md_path}")

    if args.json:
        print(json.dumps({"meta": meta, "aggregate": agg, "cases": results,
                          "dry_run": dry_run_dump}, ensure_ascii=False, indent=2))

    if any(r.get("outcome") == rp.OUTCOME_JUDGE_ERROR for r in results):
        return 1
    if agg["metrics"]["candidate_fact_fail"] or agg["metrics"]["both_fact_fail"]:
        return 1
    if args.fail_on_regression and agg["metrics"]["baseline_wins"]:
        return 1
    return 0


# --------------------------------------------------------------------------
# dry-run
# --------------------------------------------------------------------------

def build_dry_run_dump(case: BenchmarkCase, baseline: RunOutput, candidate: RunOutput,
                       args) -> Dict[str, Any]:
    order_first, order_second = pw.build_orders(args.seed)
    user_text = pw.render_pairwise_input(
        jd_text=case.jd_text(), ground_truth=case.fact_store(),
        source_resume=case.source_resume_text(),
        candidate_a=baseline.resume_text, candidate_b=candidate.resume_text,
        context=case.context_text())
    return {
        "case_id": case.case_id,
        "role_family": case.role_family,
        "inputs_resolved": {key: str(case.path_for(key)) for key in case.inputs},
        "source_resume_chars": len(case.source_resume_text()),
        "jd_chars": len(case.jd_text()),
        "fact_count": len(sc.collect_fact_ids(case.fact_store())),
        "must_preserve": case.must_preserve(),
        "forbidden_claims": case.forbidden_claims(),
        "capability_count": len(case.capabilities()),
        "round1_order": {"A": order_first[0], "B": order_first[1]},
        "round2_order": {"A": order_second[0], "B": order_second[1]},
        "judge_input_chars": len(user_text),
        "judge_input_preview": user_text[:600],
    }


def print_dry_run(args, cases: Sequence[BenchmarkCase],
                  dump: Sequence[Dict[str, Any]]) -> None:
    print("=" * 72)
    print("quality benchmark — DRY RUN（不调用 Judge）")
    print("=" * 72)
    print(f"\nbenchmark cases: {len(cases)}")
    for item in dump:
        print(f"\n[{item['case_id']}] {item['role_family']}")
        for key, value in item["inputs_resolved"].items():
            print(f"    {key:<14} {value}")
        print(f"    facts={item['fact_count']} capabilities={item['capability_count']} "
              f"jd_chars={item['jd_chars']}")
        print(f"    round1  A={item['round1_order']['A']:<9} B={item['round1_order']['B']}")
        print(f"    round2  A={item['round2_order']['A']:<9} B={item['round2_order']['B']}"
              f"   {color('（镜像顺序，可检测 position bias）', DIM)}")
        print(f"    judge input: {item['judge_input_chars']} chars")
    print("\n" + "-" * 72)
    print("dry-run 已完成：benchmark 可加载、路径可解析、A/B 顺序已镜像。")
    print("真实评测请去掉 --dry-run，或先用 --mock 验证整条流水线。")


# --------------------------------------------------------------------------
# 控制台输出
# --------------------------------------------------------------------------

def print_console(meta: Dict[str, Any], agg: Dict[str, Any],
                  results: Sequence[Dict[str, Any]]) -> None:
    metrics = agg["metrics"]
    print("=" * 72)
    print(f"quality benchmark — {meta['baseline_version']} vs {meta['candidate_version']}")
    print("=" * 72)
    print(f"benchmark={meta['benchmark_version']}  cases={meta['benchmark_cases']}  "
          f"mode={meta['mode']}  judge={meta['judge_provider']}/{meta['judge_model']}")
    if meta["judge_provider"] == "mock":
        print(color("mock judge：确定性启发式，不是真实质量判断，不能作为版本结论。", YELLOW))
    print(f"pairwise prompt={meta['pairwise_prompt']}  fact gate={meta['fact_gate']}  "
          f"seed={meta['seed']}")

    print("\n[cases]")
    for result in sorted(results, key=lambda r: str(r["case_id"])):
        outcome = str(result.get("outcome"))
        tone = {rp.OUTCOME_CANDIDATE_WIN: GREEN,
                rp.OUTCOME_BASELINE_WIN: RED,
                rp.OUTCOME_TIE: DIM,
                rp.OUTCOME_DRY_RUN: DIM}.get(outcome, YELLOW)
        print(f"  {color(outcome, tone):<32} {result['case_id']:<26} "
              f"[{result['role_family']}] decided_by={result.get('decided_by')}")

    print("\n[metrics]")
    rows = [
        ("Total Cases", metrics["total_cases"]),
        ("Candidate Wins", metrics["candidate_wins"]),
        ("Baseline Wins", metrics["baseline_wins"]),
        ("Ties", metrics["ties"]),
        ("Inconsistent Cases", metrics["position_inconsistent"]),
        ("Both Fact Fail", metrics["both_fact_fail"]),
        ("Candidate Fact Fail", metrics["candidate_fact_fail"]),
        ("Baseline Fact Fail", metrics["baseline_fact_fail"]),
        ("Judge Execution Failure", metrics["judge_errors"]),
        ("Valid Comparable Cases", metrics["valid_comparable_cases"]),
    ]
    for label, value in rows:
        print(f"  {label:<28} {value}")
    for label, key in (("Candidate Win Rate", "candidate_win_rate"),
                       ("Candidate Loss Rate", "candidate_loss_rate"),
                       ("Tie Rate", "tie_rate"),
                       ("Regression Rate", "regression_rate"),
                       ("Position Inconsistency Rate", "position_inconsistency_rate")):
        value = metrics[key]
        text = "n/a" if value is None else f"{value * 100:.1f}%"
        print(f"  {label:<28} {text}")

    print("\n[drill-down] by role family")
    for row in agg.get("by_role_family") or []:
        note = "  sample size too small" if row["small_sample"] else ""
        rate = "n/a" if row["win_rate"] is None else f"{row['win_rate'] * 100:.0f}%"
        print(f"  {row['group']:<22} {row['wins']}/{row['comparable']} wins  {rate}{note}")

    regression = agg.get("regression_cases") or []
    if regression:
        print("\n[regression] candidate 相对 baseline 退化：")
        for case_id in regression:
            print(f"  - {case_id}")
    inconsistent = agg.get("inconsistent_cases") or []
    if inconsistent:
        print("\n[position-inconsistent] 已排除出主 Win Rate：")
        for case_id in inconsistent:
            print(f"  - {case_id}")
    violations = agg.get("fact_violation_cases") or []
    if violations:
        print("\n[fact violations]")
        for item in violations:
            print(f"  - {item['case_id']} / {item['side']}: {', '.join(item['codes'])}")

    human = agg.get("human_agreement") or {}
    print(f"\n[human calibration] labelled={human.get('labelled_cases', 0)} "
          f"agreement=" + ("n/a" if human.get("agreement_rate") is None
                           else f"{human['agreement_rate'] * 100:.0f}%"))


if __name__ == "__main__":
    raise SystemExit(main())
