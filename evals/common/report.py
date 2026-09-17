#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quality Eval 结果聚合与报告输出。

输入是每个 case 的结果 dict（见 `CaseOutcome` 字段约定），输出：

    evals/reports/<date>-<baseline>-vs-<candidate>.json
    evals/reports/<date>-<baseline>-vs-<candidate>.md

约定：
- 分子分母都显式写出来，不做隐式归一化；
- Position Inconsistent / Both Fact Fail / Judge 失败从 Win Rate 分母里剔除，
  但原始总数与原始结果一律保留，不删失败样本；
- 样本量小的时候必须写出 `sample size too small`，不用小样本下强结论。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .schemas import DIMENSION_LABELS, PAIRWISE_DIMENSIONS

# --------------------------------------------------------------------------
# Outcome 枚举
# --------------------------------------------------------------------------

OUTCOME_CANDIDATE_WIN = "CANDIDATE_WIN"
OUTCOME_BASELINE_WIN = "BASELINE_WIN"
OUTCOME_TIE = "TIE"
OUTCOME_POSITION_INCONSISTENT = "POSITION_INCONSISTENT"
OUTCOME_BOTH_FACT_FAIL = "BOTH_FACT_FAIL"
OUTCOME_CANDIDATE_FACT_FAIL = "CANDIDATE_FACT_FAIL"
OUTCOME_BASELINE_FACT_FAIL = "BASELINE_FACT_FAIL"
OUTCOME_JUDGE_ERROR = "JUDGE_ERROR"
OUTCOME_DRY_RUN = "DRY_RUN"

COMPARABLE_OUTCOMES = (
    OUTCOME_CANDIDATE_WIN,
    OUTCOME_BASELINE_WIN,
    OUTCOME_TIE,
)

OUTCOME_LABELS = {
    OUTCOME_CANDIDATE_WIN: "candidate 胜",
    OUTCOME_BASELINE_WIN: "baseline 胜",
    OUTCOME_TIE: "平局",
    OUTCOME_POSITION_INCONSISTENT: "A/B 顺序不一致",
    OUTCOME_BOTH_FACT_FAIL: "双方均事实失败",
    OUTCOME_CANDIDATE_FACT_FAIL: "candidate 事实失败",
    OUTCOME_BASELINE_FACT_FAIL: "baseline 事实失败",
    OUTCOME_JUDGE_ERROR: "judge 执行失败",
    OUTCOME_DRY_RUN: "dry-run（未评测）",
}

MIN_SAMPLE_FOR_CLAIM = 5


def _safe_rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _fmt_rate(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


# --------------------------------------------------------------------------
# 聚合
# --------------------------------------------------------------------------

def aggregate(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    counts: Dict[str, int] = {}
    for result in results:
        outcome = str(result.get("outcome") or OUTCOME_JUDGE_ERROR)
        counts[outcome] = counts.get(outcome, 0) + 1

    candidate_wins = counts.get(OUTCOME_CANDIDATE_WIN, 0)
    baseline_wins = counts.get(OUTCOME_BASELINE_WIN, 0)
    ties = counts.get(OUTCOME_TIE, 0)
    inconsistent = counts.get(OUTCOME_POSITION_INCONSISTENT, 0)
    both_fail = counts.get(OUTCOME_BOTH_FACT_FAIL, 0)
    cand_fail = counts.get(OUTCOME_CANDIDATE_FACT_FAIL, 0)
    base_fail = counts.get(OUTCOME_BASELINE_FACT_FAIL, 0)
    judge_errors = counts.get(OUTCOME_JUDGE_ERROR, 0)

    comparable = candidate_wins + baseline_wins + ties
    judged = candidate_wins + baseline_wins + ties + inconsistent

    gate_decided_wins = sum(1 for r in results
                            if r.get("outcome") == OUTCOME_CANDIDATE_WIN
                            and r.get("decided_by") == "fact_gate")

    metrics = {
        "total_cases": total,
        "candidate_wins": candidate_wins,
        "baseline_wins": baseline_wins,
        "ties": ties,
        "position_inconsistent": inconsistent,
        "both_fact_fail": both_fail,
        "candidate_fact_fail": cand_fail,
        "baseline_fact_fail": base_fail,
        "judge_errors": judge_errors,
        "valid_comparable_cases": comparable,
        "judged_cases": judged,
        "gate_decided_wins": gate_decided_wins,
        "candidate_win_rate": _safe_rate(candidate_wins, comparable),
        "candidate_loss_rate": _safe_rate(baseline_wins, comparable),
        "tie_rate": _safe_rate(ties, comparable),
        "position_inconsistency_rate": _safe_rate(inconsistent, judged),
        "regression_rate": _safe_rate(baseline_wins, comparable),
        "fact_gate_pass_rate_candidate": _safe_rate(
            total - cand_fail - both_fail, total),
        "fact_gate_pass_rate_baseline": _safe_rate(
            total - base_fail - both_fail, total),
    }

    return {
        "metrics": metrics,
        "outcome_counts": counts,
        "by_role_family": _drilldown(results, lambda r: str(r.get("role_family") or "unknown")),
        "by_difficulty": _drilldown(results, lambda r: str(r.get("difficulty") or "unknown")),
        "by_source": _drilldown(results, lambda r: str(r.get("source") or "unknown")),
        "dimension_wins": _dimension_wins(results),
        "regression_cases": [str(r["case_id"]) for r in results
                             if r.get("outcome") == OUTCOME_BASELINE_WIN],
        "inconsistent_cases": [str(r["case_id"]) for r in results
                               if r.get("outcome") == OUTCOME_POSITION_INCONSISTENT],
        "fact_violation_cases": _fact_violation_cases(results),
        "judge_error_cases": [str(r["case_id"]) for r in results
                              if r.get("outcome") == OUTCOME_JUDGE_ERROR],
        "human_agreement": _human_agreement(results),
    }


def _drilldown(results: Sequence[Dict[str, Any]],
               key_fn) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for result in results:
        buckets.setdefault(key_fn(result), []).append(result)
    rows: List[Dict[str, Any]] = []
    for name in sorted(buckets):
        group = buckets[name]
        wins = sum(1 for r in group if r.get("outcome") == OUTCOME_CANDIDATE_WIN)
        losses = sum(1 for r in group if r.get("outcome") == OUTCOME_BASELINE_WIN)
        ties = sum(1 for r in group if r.get("outcome") == OUTCOME_TIE)
        comparable = wins + losses + ties
        rows.append({
            "group": name,
            "cases": len(group),
            "wins": wins,
            "losses": losses,
            "ties": ties,
            "comparable": comparable,
            "win_rate": _safe_rate(wins, comparable),
            "small_sample": len(group) < MIN_SAMPLE_FOR_CLAIM,
        })
    return rows


def _dimension_wins(results: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """维度倾向。只统计位置一致的 case——位置不一致时维度结论同样不可信。

    case result 里的 `dimensions` 形如 {dim: "baseline" | "candidate" | "tie"}，
    标签已由 pairwise 层映射回真实版本，不携带 A/B 身份。
    """
    out: Dict[str, Dict[str, int]] = {}
    for dim in PAIRWISE_DIMENSIONS:
        out[dim] = {"candidate": 0, "baseline": 0, "tie": 0}
    for result in results:
        if not result.get("rounds"):
            continue
        if result.get("position_consistency") is not True:
            continue
        for dim, winner in (result.get("dimensions") or {}).items():
            if dim not in out:
                continue
            if winner == "candidate":
                out[dim]["candidate"] += 1
            elif winner == "baseline":
                out[dim]["baseline"] += 1
            else:
                out[dim]["tie"] += 1
    return out


def _fact_violation_cases(results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for result in results:
        gate = result.get("fact_gate") or {}
        for side in ("baseline", "candidate"):
            entry = gate.get(side) or {}
            if entry.get("passed", True):
                continue
            out.append({
                "case_id": result.get("case_id"),
                "side": side,
                "categories": sorted({str(i.get("category")) for i in (entry.get("violations") or [])}),
                "codes": sorted({str(i.get("code")) for i in (entry.get("violations") or [])}),
                "messages": [str(i.get("message")) for i in (entry.get("violations") or [])][:5],
            })
    return out


def _human_agreement(results: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """LLM Judge 与人工标注的一致率（只统计双方都给出结论的 case）。

    Judge 不是 Ground Truth，这个数字是校准信号，不是正确率断言。
    """
    total = 0
    agree = 0
    details: List[Dict[str, Any]] = []
    for result in results:
        human = result.get("human_review") or {}
        human_winner = human.get("winner")
        if not human_winner:
            continue
        if result.get("outcome") not in COMPARABLE_OUTCOMES:
            continue
        judge_winner = {
            OUTCOME_CANDIDATE_WIN: "candidate",
            OUTCOME_BASELINE_WIN: "baseline",
            OUTCOME_TIE: "Tie",
        }[result["outcome"]]
        mapped_human = {"A": "candidate", "B": "baseline", "Tie": "Tie"}.get(
            str(human_winner), str(human_winner))
        total += 1
        same = mapped_human == judge_winner
        agree += 1 if same else 0
        details.append({"case_id": result.get("case_id"), "human": mapped_human,
                        "judge": judge_winner, "agree": same})
    return {
        "labelled_cases": total,
        "agreements": agree,
        "agreement_rate": _safe_rate(agree, total),
        "details": details,
        "note": ("human_review.winner 是盲评标签：A = baseline 产物，B = candidate 产物；"
                 "Tie = 平局"),
    }


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------

def build_meta(run_meta: Dict[str, Any]) -> Dict[str, Any]:
    meta = dict(run_meta)
    meta.setdefault("generated_at", datetime.now().isoformat(timespec="seconds"))
    return meta


def report_basename(meta: Dict[str, Any]) -> str:
    stamp = str(meta.get("generated_at") or datetime.now().isoformat())[:10]
    baseline = str(meta.get("baseline_version") or "baseline")
    candidate = str(meta.get("candidate_version") or "candidate")
    safe = lambda text: "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text)
    name = f"{stamp}-{safe(baseline)}-vs-{safe(candidate)}"
    mode = str(meta.get("mode") or "real")
    if mode not in ("real", ""):
        # mock / dry-run 报告不能用真实评测的文件名，避免被当成有效结论。
        name += f"-{safe(mode)}"
        if mode == "mock" and meta.get("mock_mode"):
            name += f"-{safe(str(meta['mock_mode']))}"
    return name


def render_markdown(meta: Dict[str, Any], agg: Dict[str, Any],
                    results: Sequence[Dict[str, Any]]) -> str:
    metrics = agg["metrics"]
    lines: List[str] = []
    add = lines.append

    add(f"# Quality Eval Report — {meta.get('baseline_version')} vs {meta.get('candidate_version')}")
    add("")
    add("> 本报告回答的是「两个版本都没有明显事实错误时，哪个 Skill 版本生成的简历更有效」。")
    add("> 它不替代 Regression Eval：回归守住下限，这里判断上限。")
    add("")

    add("## 0. 运行元信息（可复现性）")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    for key, label in (
        ("generated_at", "run timestamp"),
        ("baseline_version", "baseline version"),
        ("candidate_version", "candidate version"),
        ("skill_version", "Skill version"),
        ("benchmark_version", "benchmark version"),
        ("benchmark_cases", "benchmark cases"),
        ("mode", "run mode"),
        ("judge_provider", "judge provider"),
        ("judge_model", "judge model"),
        ("judge_temperature", "judge temperature"),
        ("judge_max_tokens", "judge max_tokens"),
        ("judge_endpoint", "judge endpoint"),
        ("judge_api_key_present", "api key present"),
        ("pairwise_prompt", "pairwise prompt"),
        ("fact_guard_prompt", "fact guard prompt"),
        ("evidence_prompt", "evidence prompt"),
        ("fact_gate", "fact gate"),
        ("position_swap", "position swap"),
        ("seed", "random seed"),
    ):
        if key in meta:
            add(f"| {label} | {meta.get(key)} |")
    add("")

    mode = str(meta.get("mode") or "real")
    warning = ""
    if mode == "dry-run":
        warning = ("**⚠ dry-run：未调用 Judge，本报告不包含任何版本比较结论，"
                   "仅用于验证 Harness。**")
    elif mode == "mock" or meta.get("judge_provider") == "mock":
        warning = ("**⚠ mock 模式：产物与判定均由确定性 fixture 合成，"
                   "不是真实质量判断，任何版本结论都不能基于本报告。**")
    if warning:
        add(warning)
        add("")

    add("## 1. 版本比较指标")
    add("")
    add("| 指标 | 值 |")
    add("|---|---|")
    add(f"| Total Cases | {metrics['total_cases']} |")
    add(f"| Candidate Wins | {metrics['candidate_wins']} |")
    add(f"| Baseline Wins | {metrics['baseline_wins']} |")
    add(f"| Ties | {metrics['ties']} |")
    add(f"| Inconsistent Cases | {metrics['position_inconsistent']} |")
    add(f"| Both Fact Fail | {metrics['both_fact_fail']} |")
    add(f"| Candidate Fact Fail | {metrics['candidate_fact_fail']} |")
    add(f"| Baseline Fact Fail | {metrics['baseline_fact_fail']} |")
    add(f"| Judge Execution Failure | {metrics['judge_errors']} |")
    add(f"| Valid Comparable Cases | {metrics['valid_comparable_cases']} |")
    add("")
    add("| 比率 | 值 | 分子 / 分母 |")
    add("|---|---|---|")
    add(f"| Candidate Win Rate | {_fmt_rate(metrics['candidate_win_rate'])} | "
        f"{metrics['candidate_wins']} / {metrics['valid_comparable_cases']} |")
    add(f"| Candidate Loss Rate | {_fmt_rate(metrics['candidate_loss_rate'])} | "
        f"{metrics['baseline_wins']} / {metrics['valid_comparable_cases']} |")
    add(f"| Tie Rate | {_fmt_rate(metrics['tie_rate'])} | "
        f"{metrics['ties']} / {metrics['valid_comparable_cases']} |")
    add(f"| Regression Rate | {_fmt_rate(metrics['regression_rate'])} | "
        f"{metrics['baseline_wins']} / {metrics['valid_comparable_cases']} |")
    add(f"| Position Inconsistency Rate | {_fmt_rate(metrics['position_inconsistency_rate'])} "
        f"| {metrics['position_inconsistent']} / {metrics['judged_cases']} |")
    add(f"| Candidate Fact Gate Pass Rate | {_fmt_rate(metrics['fact_gate_pass_rate_candidate'])}"
        f" | {metrics['total_cases'] - metrics['candidate_fact_fail'] - metrics['both_fact_fail']}"
        f" / {metrics['total_cases']} |")
    add(f"| Baseline Fact Gate Pass Rate | {_fmt_rate(metrics['fact_gate_pass_rate_baseline'])}"
        f" | {metrics['total_cases'] - metrics['baseline_fact_fail'] - metrics['both_fact_fail']}"
        f" / {metrics['total_cases']} |")
    add("")
    add(f"`valid_comparable_cases` 已排除 Position Inconsistent / Both Fact Fail / "
        f"Judge Execution Failure；其中 {metrics['gate_decided_wins']} 个胜利由 Fact Gate 直接判定。")
    add("")

    add("## 2. Drill-down")
    add("")
    for title, key in (("By Role Family", "by_role_family"),
                       ("By Difficulty", "by_difficulty"),
                       ("By Case Source", "by_source")):
        rows = agg.get(key) or []
        if not rows:
            continue
        add(f"### {title}")
        add("")
        add("| group | cases | wins | losses | ties | win rate | note |")
        add("|---|---|---|---|---|---|---|")
        for row in rows:
            note = "sample size too small" if row["small_sample"] else ""
            add(f"| {row['group']} | {row['cases']} | {row['wins']} | {row['losses']} | "
                f"{row['ties']} | {_fmt_rate(row['win_rate'])} | {note} |")
        add("")

    dims = agg.get("dimension_wins") or {}
    if any(sum(v.values()) for v in dims.values()):
        add("### Judge 维度倾向（仅位置一致的 case）")
        add("")
        add("| 维度 | candidate | baseline | tie |")
        add("|---|---|---|---|")
        for dim in PAIRWISE_DIMENSIONS:
            counts = dims.get(dim) or {}
            add(f"| {DIMENSION_LABELS.get(dim, dim)} | {counts.get('candidate', 0)} | "
                f"{counts.get('baseline', 0)} | {counts.get('tie', 0)} |")
        add("")

    add("## 3. Fact Safety（Hard Gate）")
    add("")
    violations = agg.get("fact_violation_cases") or []
    if not violations:
        add("所有 case 双方均通过 Fact Gate。")
    else:
        add("事实错误不会被表达质量抵消：出现以下违规的版本在该 case 中直接判负。")
        add("")
        add("| case | side | 违规类别 | issue codes |")
        add("|---|---|---|---|")
        for item in violations:
            add(f"| {item['case_id']} | {item['side']} | {', '.join(item['categories'])} | "
                f"{', '.join(item['codes'])} |")
    add("")

    if any(r.get("evidence_judge") for r in results):
        add("## 3b. JD Evidence Judge（LLM 诊断，不参与胜负判定）")
        add("")
        add("分母只含候选人真实拥有的高价值 evidence；候选人本来没有的能力不参与计算。")
        add("")
        add("| case | side | LLM evidence recall | available | used | missed |")
        add("|---|---|---|---|---|---|")
        for result in sorted(results, key=lambda r: str(r.get("case_id"))):
            for side in ("baseline", "candidate"):
                entry = (result.get("evidence_judge") or {}).get(side) or {}
                if not entry:
                    continue
                if entry.get("error"):
                    add(f"| {result.get('case_id')} | {side} | error | — | — | "
                        f"{str(entry['error'])[:60]} |")
                    continue
                recall = entry.get("evidence_recall")
                recall_text = "n/a" if recall is None else f"{float(recall) * 100:.0f}%"
                missed = ", ".join(str(item.get("fact")) for item in
                                   (entry.get("missed_high_value_evidence") or [])) or "—"
                add(f"| {result.get('case_id')} | {side} | {recall_text} | "
                    f"{entry.get('available_high_value_evidence')} | "
                    f"{entry.get('used_high_value_evidence')} | {missed} |")
        add("")

    add("## 4. Regression Cases（candidate 相对 baseline 退化）")
    add("")
    regression = agg.get("regression_cases") or []
    if regression:
        add("这些 case 上 baseline 更好，需要逐个排查原因：")
        add("")
        for case_id in regression:
            add(f"- `{case_id}`")
    else:
        add("无。")
    add("")

    add("## 5. Position Inconsistent Cases")
    add("")
    inconsistent = agg.get("inconsistent_cases") or []
    if inconsistent:
        add("A/B 顺序交换后 Judge 结论翻转，说明存在位置偏差，已从主 Win Rate 中剔除：")
        add("")
        for case_id in inconsistent:
            add(f"- `{case_id}`")
    else:
        add("无。")
    add("")

    human = agg.get("human_agreement") or {}
    add("## 6. Human Calibration")
    add("")
    if human.get("labelled_cases"):
        add(f"已标注 {human['labelled_cases']} 个 case，"
            f"LLM Judge 与人工一致率 {_fmt_rate(human.get('agreement_rate'))}。")
        add("")
        add("| case | human | judge | agree |")
        add("|---|---|---|---|")
        for item in human.get("details") or []:
            add(f"| {item['case_id']} | {item['human']} | {item['judge']} | "
                f"{'✓' if item['agree'] else '✗'} |")
    else:
        add("尚无人工标注。Judge 不是 Ground Truth：建议抽取 10%–20% 的 case "
            "填写 `human_review.winner` 后重新运行本 Eval。")
    add("")

    add("## 7. Case Results")
    add("")
    add("| case | role family | outcome | decided by | 位置一致 | candidate gate | "
        "baseline gate | evidence recall (c/b) |")
    add("|---|---|---|---|---|---|---|---|")
    for result in sorted(results, key=lambda r: str(r.get("case_id"))):
        gate = result.get("fact_gate") or {}
        recall = result.get("evidence_recall") or {}
        add(f"| {result.get('case_id')} | {result.get('role_family')} | "
            f"{OUTCOME_LABELS.get(str(result.get('outcome')), result.get('outcome'))} | "
            f"{result.get('decided_by')} | "
            f"{_tick(result.get('position_consistency'))} | "
            f"{_tick((gate.get('candidate') or {}).get('passed'))} | "
            f"{_tick((gate.get('baseline') or {}).get('passed'))} | "
            f"{_fmt_recall((recall.get('candidate') or {}))} / "
            f"{_fmt_recall((recall.get('baseline') or {}))} |")
    add("")

    add("## 8. Judge Reasons")
    add("")
    for result in sorted(results, key=lambda r: str(r.get("case_id"))):
        overall = (result.get("overall") or {})
        if not overall:
            continue
        add(f"### {result.get('case_id')} — {OUTCOME_LABELS.get(str(result.get('outcome')), '')}")
        add("")
        add(f"- overall: {_side_label(overall.get('winner'))} "
            f"(confidence {overall.get('confidence')})")
        if overall.get("reason"):
            add(f"  - {overall['reason']}")
        dimensions = result.get("dimensions") or {}
        for dim in PAIRWISE_DIMENSIONS:
            label = dimensions.get(dim)
            if not label:
                continue
            add(f"- {DIMENSION_LABELS.get(dim, dim)}: {_side_label(label)}")
        for note in result.get("notes") or []:
            add(f"- note: {note}")
        add("")

    return "\n".join(lines).rstrip() + "\n"


def _tick(value: Any) -> str:
    if value is None:
        return "—"
    return "✓" if value else "✗"


def _fmt_recall(entry: Dict[str, Any]) -> str:
    rate = entry.get("recall")
    if rate is None:
        return "n/a"
    return f"{rate * 100:.0f}%"


def _side_label(winner: Any) -> str:
    mapping = {
        "A": "A",
        "B": "B",
        "Tie": "Tie",
        "baseline": "baseline",
        "candidate": "candidate",
        "tie": "Tie",
    }
    return mapping.get(str(winner), str(winner))


# --------------------------------------------------------------------------
# 落盘
# --------------------------------------------------------------------------

def write_reports(out_dir: Path, meta: Dict[str, Any], agg: Dict[str, Any],
                  results: Sequence[Dict[str, Any]]) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    basename = report_basename(meta)
    json_path = out_dir / f"{basename}.json"
    md_path = out_dir / f"{basename}.md"

    payload = {
        "meta": meta,
        "aggregate": agg,
        "cases": list(results),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(meta, agg, results), encoding="utf-8")
    return json_path, md_path
