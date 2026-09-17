#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Blind Pairwise Judge 与 Position Swap。

为什么不用绝对分
----------------
「匹配度 87」这类综合分不可比较：不同 JD 难度不同、Judge 每次绝对分会漂移、
84 与 87 的差异解释不清、跨 case 的绝对分不能直接比较。所以版本优劣采用
**盲评 A/B**：同一份事实、同一份 JD、两个版本各出一份简历，谁更好。

为什么要 A/B swap
-----------------
只跑一个顺序无法区分「B 真的更好」和「Judge 偏爱位置 B」。因此每个 case 至少跑两轮：

    Run 1:  A = baseline , B = candidate
    Run 2:  A = candidate, B = baseline

再映射回真实版本：

    Run1 winner=B (candidate) 且 Run2 winner=A (candidate)  → CONSISTENT candidate win
    Run1 winner=A (baseline)  且 Run2 winner=A (candidate)  → POSITION_INCONSISTENT

`POSITION_INCONSISTENT` 不计入版本胜负：那说明 Judge 更可能偏爱位置而不是内容。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..common.judge_client import JudgeClient, JudgeError, attach_mock_terms
from ..common.schemas import (PAIRWISE_DIMENSIONS, extract_json_object,
                              normalize_winner, validate_pairwise_result)
from .render import build_pairwise_payload, render_pairwise_input

LABEL_BASELINE = "baseline"
LABEL_CANDIDATE = "candidate"
LABEL_TIE = "tie"


@dataclass
class RoundRecord:
    index: int
    order: Dict[str, str]
    winner_side: Optional[str] = None
    winner_label: Optional[str] = None
    dimensions: Dict[str, str] = field(default_factory=dict)
    confidence: Optional[float] = None
    overall_reason: str = ""
    error: Optional[str] = None
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "order": self.order,
            "winner_side": self.winner_side,
            "winner_label": self.winner_label,
            "dimensions": self.dimensions,
            "confidence": self.confidence,
            "overall_reason": self.overall_reason,
            "error": self.error,
            "raw_output": self.raw_output[:4000],
        }


@dataclass
class PairwiseOutcome:
    rounds: List[RoundRecord] = field(default_factory=list)
    position_consistent: Optional[bool] = None
    winner: Optional[str] = None  # baseline | candidate | tie | None
    dimensions: Dict[str, str] = field(default_factory=dict)
    overall: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    seed: int = 0
    first_order: Tuple[str, str] = (LABEL_BASELINE, LABEL_CANDIDATE)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rounds": [r.to_dict() for r in self.rounds],
            "position_consistent": self.position_consistent,
            "winner": self.winner,
            "dimensions": self.dimensions,
            "overall": self.overall,
            "error": self.error,
            "seed": self.seed,
            "first_order": list(self.first_order),
        }


def build_orders(seed: int) -> Tuple[Tuple[str, str], Tuple[str, str]]:
    """两轮镜像顺序。seed 只决定第一轮谁在 A 位，第二轮永远是其镜像。"""
    rng = random.Random(seed)
    if rng.random() < 0.5:
        first: Tuple[str, str] = (LABEL_CANDIDATE, LABEL_BASELINE)
    else:
        first = (LABEL_BASELINE, LABEL_CANDIDATE)
    return first, (first[1], first[0])


def run_round(index: int, order: Tuple[str, str], texts: Dict[str, str],
              client: JudgeClient, system_prompt: str, jd_text: str,
              ground_truth: Dict[str, Any], source_resume: str, context: str,
              capabilities: Sequence[Dict[str, Any]],
              ats_keywords: Sequence[str], case_id: str) -> RoundRecord:
    side_label = {"A": order[0], "B": order[1]}
    record = RoundRecord(index=index, order={"A": order[0], "B": order[1]})

    user_text = render_pairwise_input(
        jd_text=jd_text, ground_truth=ground_truth, source_resume=source_resume,
        candidate_a=texts[order[0]], candidate_b=texts[order[1]], context=context)
    payload = build_pairwise_payload(
        candidates={"A": texts[order[0]], "B": texts[order[1]]},
        dimensions=PAIRWISE_DIMENSIONS, capabilities=capabilities,
        ats_keywords=ats_keywords, case_id=f"{case_id}#round{index}")

    try:
        raw = client.complete(system_prompt, user_text, payload)
    except JudgeError as exc:
        record.error = str(exc)
        return record

    record.raw_output = raw or ""
    data = extract_json_object(raw or "")
    if data is None:
        record.error = "judge 输出无法解析为 JSON object"
        return record

    problems = validate_pairwise_result(data)
    if problems:
        record.error = "judge 输出结构非法: " + "; ".join(problems[:4])
        return record

    overall = data.get("overall") or {}
    winner_side = normalize_winner(overall.get("winner"))
    record.winner_side = winner_side
    record.winner_label = (LABEL_TIE if winner_side == "Tie"
                           else side_label.get(str(winner_side)))
    record.confidence = float(overall.get("confidence") or 0.0)
    record.overall_reason = str(overall.get("reason") or "")

    for dim in PAIRWISE_DIMENSIONS:
        entry = data.get(dim) or {}
        side = normalize_winner(entry.get("winner"))
        record.dimensions[dim] = (LABEL_TIE if side == "Tie"
                                 else side_label.get(str(side)) or LABEL_TIE)
    return record


def run_pairwise(case_id: str, texts: Dict[str, str], client: JudgeClient,
                 system_prompt: str, jd_text: str, ground_truth: Dict[str, Any],
                 source_resume: str, context: str = "",
                 capabilities: Sequence[Dict[str, Any]] = (),
                 ats_keywords: Sequence[str] = (), seed: int = 0) -> PairwiseOutcome:
    """跑两轮镜像 A/B，并映射回真实版本。"""
    first, second = build_orders(seed)
    outcome = PairwiseOutcome(seed=seed, first_order=first)
    attach_mock_terms(client, [str(s.get("label") or "") for s in capabilities],
                      ats_keywords)

    total = 0.0
    for index, order in enumerate((first, second), start=1):
        record = run_round(index, order, texts, client, system_prompt, jd_text,
                           ground_truth, source_resume, context, capabilities,
                           ats_keywords, case_id)
        outcome.rounds.append(record)
        if record.error:
            outcome.error = f"round {index}: {record.error}"
            return outcome
        total += record.confidence or 0.0

    labels = [r.winner_label for r in outcome.rounds]
    if labels[0] == labels[1]:
        outcome.position_consistent = True
        outcome.winner = labels[0]
    else:
        outcome.position_consistent = False
        outcome.winner = None

    if outcome.winner is not None:
        outcome.dimensions = dict(outcome.rounds[0].dimensions)
        outcome.overall = {
            "winner": outcome.winner,
            "confidence": round(min(0.99, total / 2.0), 3),
            "reason": outcome.rounds[0].overall_reason,
        }
    else:
        outcome.overall = {
            "winner": "inconsistent",
            "confidence": None,
            "reason": ("A/B 顺序交换后结论翻转："
                       f"round1={labels[0]}, round2={labels[1]}，"
                       "说明存在位置偏差，已排除出主 Win Rate。"),
        }
    return outcome
