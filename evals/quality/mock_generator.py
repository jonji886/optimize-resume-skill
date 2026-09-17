#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线 mock 产物生成器（只为验证 Harness，不是真的 Skill 输出）。

为什么需要它
------------
Eval 系统在没有 API Key、没有真实 baseline / candidate 产物时也必须能跑通并自检。
本模块从 case 的 ground truth fact store 合成两份**结构性产物**：

    policy="strong"  高价值 evidence 前置、覆盖全部可用 evidence、简洁
    policy="weak"    只覆盖显眼的高权重 evidence、丢掉项目类 evidence、冗余表达

两个 policy 都严格在事实边界内生成（不新增事实、不使用 denied / unknown 事实、
动词级别不超过 fact 的 scope），因此可以同时用来验证：

    strong vs weak    → 必须判出 strong 一方获胜
    strong vs strong  → 必须判出 Tie
    weak   vs strong  → 必须判出 baseline 获胜（回归检测）

这就是 `--mock-mode candidate-wins / equal / baseline-wins` 三种已知答案自检。

注意：生成的产物**不是**真实简历，也不用于任何真实质量结论。它省略了 fact store
没有覆盖的章节（如教育背景），因为凭空补一段经历本身就是我们要防的违规。
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import validate_claims as vc  # noqa: E402

POLICY_STRONG = "strong"
POLICY_WEAK = "weak"

LEVEL_VERB = {
    "owner": "独立完成",
    "lead": "主导",
    "core_execution": "负责",
    "support": "参与",
    "unspecified": "参与",
}

MIGRATION_TAIL = "（可迁移至目标岗位场景）"
WEAK_PADDING = "，并持续推动问题闭环与经验沉淀"

PROJECT_TYPE_LABELS = {
    "work_project": "工作项目",
    "personal_project": "个人项目",
    "internal_tool": "内部工具",
    "demo": "Demo",
    "open_source": "开源项目",
}


@dataclass
class GeneratedOutput:
    case_id: str
    policy: str
    resume_text: str
    facts: Dict[str, Any]
    skipped_facts: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 事实 -> 表述
# --------------------------------------------------------------------------

def _strip_leading_verb(statement: str) -> str:
    for _level, verbs in vc.SCOPE_VERBS:
        for verb in sorted(verbs, key=len, reverse=True):
            if statement.startswith(verb):
                return statement[len(verb):].lstrip("，,、 ")
    return statement


def build_claim_text(fact: Dict[str, Any]) -> Optional[str]:
    """把 fact 转成一条 claim 文本；返回 None 表示这条 fact 不该被写进简历。"""
    status = str(fact.get("status") or "")
    if status in ("denied", "unknown"):
        return None

    scope = str(fact.get("scope") or "unspecified")
    level = scope if scope in LEVEL_VERB else "unspecified"
    core = _strip_leading_verb(str(fact.get("statement") or "").strip())
    if not core:
        return None

    text = f"{LEVEL_VERB[level]}{core}"

    # 运行时护栏：动词级别不得高于 fact 的 scope，否则该 fact 直接跳过。
    detected, _verb = vc.detect_scope(text)
    evidence_level = vc.SCOPE_LEVELS.get(scope)
    if detected is not None and evidence_level is not None:
        if vc.SCOPE_LEVELS.get(detected, 0) > evidence_level:
            return None

    if status == "transferable":
        text += MIGRATION_TAIL
    return text


@dataclass
class CandidateFact:
    fact: Dict[str, Any]
    text: str
    label: str
    weight: int
    kind: str
    entity_id: str


def collect_candidate_facts(store: Dict[str, Any],
                            capabilities: Sequence[Dict[str, Any]]
                            ) -> Tuple[List[CandidateFact], List[str]]:
    weight_by_fact: Dict[str, int] = {}
    label_by_fact: Dict[str, str] = {}
    for cap in capabilities:
        weight = int(cap.get("weight") or 2)
        for fact_id in cap.get("evidence_facts") or []:
            fact_id = str(fact_id)
            if weight > weight_by_fact.get(fact_id, 0):
                weight_by_fact[fact_id] = weight
                label_by_fact[fact_id] = str(cap.get("label") or "")

    out: List[CandidateFact] = []
    skipped: List[str] = []
    for kind in ("experiences", "projects"):
        for entity in store.get(kind) or []:
            if not isinstance(entity, dict):
                continue
            for fact in entity.get("facts") or []:
                if not isinstance(fact, dict):
                    continue
                fact_id = str(fact.get("id") or "")
                text = build_claim_text(fact)
                if text is None:
                    skipped.append(fact_id)
                    continue
                label = label_by_fact.get(fact_id) or str(fact.get("statement") or "")[:8]
                out.append(CandidateFact(
                    fact=fact, text=text, label=label,
                    weight=weight_by_fact.get(fact_id, 0),
                    kind=kind, entity_id=str(entity.get("id") or "")))
    return out, skipped


# --------------------------------------------------------------------------
# 简历装配
# --------------------------------------------------------------------------

def _entity_order(store: Dict[str, Any], kind: str) -> List[str]:
    return [str(entity.get("id")) for entity in (store.get(kind) or [])
            if isinstance(entity, dict)]


def _bullet(label: str, text: str) -> str:
    return f"- **{label}**：{text}"


def _entity_heading(store: Dict[str, Any], kind: str, entity_id: str) -> Tuple[str, str]:
    for entity in store.get(kind) or []:
        if not isinstance(entity, dict) or str(entity.get("id")) != entity_id:
            continue
        if kind == "experiences":
            return (f"{entity.get('employer')}｜{entity.get('role')}",
                    str(entity.get("time_range") or ""))
        project_type = PROJECT_TYPE_LABELS.get(str(entity.get("type")), "项目")
        return (f"{entity.get('name')}｜{project_type}", str(entity.get("time_range") or ""))
    return (entity_id, "")


def generate(case_id: str, store: Dict[str, Any],
             capabilities: Sequence[Dict[str, Any]],
             policy: str = POLICY_STRONG) -> GeneratedOutput:
    facts, skipped = collect_candidate_facts(store, capabilities)

    if policy == POLICY_WEAK:
        # 弱版本的真实失败模式：只写最显眼的高权重工作经历证据，
        # 完全丢掉项目类证据与权重较低但仍相关的 evidence，并用冗余表达填篇幅。
        selected = [item for item in facts
                    if item.kind == "experiences" and item.weight >= 3]
        padding = WEAK_PADDING
    else:
        selected = list(facts)
        padding = ""

    # 稳定排序：权重降序，其次保持 fact store 原序。
    order_index = {item.fact.get("id"): index for index, item in enumerate(facts)}
    selected.sort(key=lambda item: (-item.weight, order_index.get(item.fact.get("id"), 0)))

    experience_items = [item for item in selected if item.kind == "experiences"]
    project_items = [item for item in selected if item.kind == "projects"]

    summary_items = experience_items[:3]
    remaining = experience_items[3:]

    lines: List[str] = []
    name = str((store.get("meta") or {}).get("candidate") or "候选人")
    lines.append(f"# {name}")
    lines.append("手机/微信：13800000000 ｜邮箱：mock@example.com")
    lines.append("## 个人优势")

    claims: List[Dict[str, Any]] = []

    def add_claim(item: CandidateFact, text: str, rank: Optional[int]) -> None:
        claim: Dict[str, Any] = {
            "id": f"claim-{len(claims) + 1:03d}",
            "text": text,
            "evidence": [str(item.fact.get("id"))],
            "claim_type": ("transferable" if str(item.fact.get("status")) == "transferable"
                           else "direct"),
        }
        if rank is not None:
            claim["relevance_rank"] = rank
        claims.append(claim)

    if summary_items:
        for rank, item in enumerate(summary_items, start=1):
            rendered = f"{item.text}{padding}"
            lines.append(_bullet(item.label, rendered))
            add_claim(item, rendered, rank)
    else:
        lines.append(_bullet("说明", "（mock fixture：无可用高权重 evidence）"))

    if policy == POLICY_WEAK and summary_items:
        # 冗余：个人优势再把工作经历里已经说过的事说一遍。
        for item in summary_items[:2]:
            rendered = f"{item.text}{padding}"
            lines.append(_bullet(item.label, rendered))
            add_claim(item, rendered, None)

    lines.append("## 工作经历")
    by_entity: Dict[str, List[CandidateFact]] = {}
    for item in remaining:
        by_entity.setdefault(item.entity_id, []).append(item)
    for entity_id in _entity_order(store, "experiences"):
        items = by_entity.get(entity_id)
        if not items:
            continue
        heading, time_range = _entity_heading(store, "experiences", entity_id)
        lines.append(f"### {heading}")
        if time_range:
            lines.append(time_range)
        for item in items:
            rendered = f"{item.text}{padding}"
            lines.append(_bullet(item.label, rendered))
            add_claim(item, rendered, None)

    if project_items:
        lines.append("## 项目经历")
        by_entity = {}
        for item in project_items:
            by_entity.setdefault(item.entity_id, []).append(item)
        for entity_id in _entity_order(store, "projects"):
            items = by_entity.get(entity_id)
            if not items:
                continue
            heading, time_range = _entity_heading(store, "projects", entity_id)
            lines.append(f"### {heading}")
            if time_range:
                lines.append(time_range)
            for item in items:
                rendered = f"{item.text}{padding}"
                lines.append(_bullet(item.label, rendered))
                add_claim(item, rendered, None)

    facts_store = copy.deepcopy(store)
    facts_store["claims"] = claims
    facts_store.setdefault("meta", {})["resume_file"] = f"{name}-mock-{policy}.md"

    output = GeneratedOutput(case_id=case_id, policy=policy,
                             resume_text="\n".join(lines) + "\n",
                             facts=facts_store, skipped_facts=skipped)
    output.notes.append("mock fixture：由 ground truth 合成，不是真实 Skill 产物")
    if skipped:
        output.notes.append(
            "按事实边界跳过（denied / unknown / 动词级别超出 scope）："
            + ", ".join(skipped))
    return output


def generate_pair(case_id: str, store: Dict[str, Any],
                  capabilities: Sequence[Dict[str, Any]],
                  mock_mode: str = "candidate-wins"
                  ) -> Tuple[GeneratedOutput, GeneratedOutput]:
    """按 mock_mode 生成 (baseline, candidate) 两份已知答案的产物。"""
    if mock_mode == "equal":
        baseline = generate(case_id, store, capabilities, POLICY_STRONG)
        candidate = generate(case_id, store, capabilities, POLICY_STRONG)
    elif mock_mode == "baseline-wins":
        baseline = generate(case_id, store, capabilities, POLICY_STRONG)
        candidate = generate(case_id, store, capabilities, POLICY_WEAK)
    else:  # candidate-wins
        baseline = generate(case_id, store, capabilities, POLICY_WEAK)
        candidate = generate(case_id, store, capabilities, POLICY_STRONG)

    baseline_label = "baseline"
    candidate_label = "candidate"
    baseline.notes.append(f"mock-mode={mock_mode} → baseline 使用 policy="
                          f"{baseline.policy}")
    candidate.notes.append(f"mock-mode={mock_mode} → candidate 使用 policy="
                           f"{candidate.policy}")
    assert baseline_label != candidate_label
    return baseline, candidate
