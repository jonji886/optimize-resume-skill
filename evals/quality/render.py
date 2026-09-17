#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Judge 输入的渲染层。

唯一职责：把 case 数据渲染成 Judge 能看的文本，并保证**上下文干净**。

干净上下文的硬性要求（不可放宽）：
- 不出现 baseline / candidate / 版本号 / 「新」「旧」等身份信息，只出现 Candidate A / B；
- 不出现生成过程的 reasoning、claims 侧车、内部标记、Skill 名称或版本；
- 不出现开发者希望哪个版本获胜的任何暗示；
- 只提供评测所需内容：Target JD、Source Facts、Source Resume、Candidate A/B、Rubric。
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

FACT_STATUS_LABELS = {
    "confirmed": "confirmed",
    "transferable": "transferable（只能表达为可迁移能力）",
    "unknown": "unknown（无证据）",
    "denied": "denied（禁止写入，且不得重新引入）",
}


def render_fact_block(store: Dict[str, Any]) -> str:
    """把 ground truth fact store 渲染成 Judge 可读文本（不含 claims）。"""
    lines: List[str] = []
    for kind in ("experiences", "projects"):
        entities = store.get(kind) or []
        if not entities:
            continue
        lines.append(f"## {kind}")
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            header = str(entity.get("employer") or entity.get("name") or entity.get("id"))
            meta_bits = [str(entity.get("role") or "").strip(),
                         str(entity.get("time_range") or "").strip()]
            if kind == "projects":
                meta_bits.insert(0, f"type={entity.get('type')}")
                if entity.get("commercial_delivery") is not None:
                    meta_bits.append(f"commercial_delivery={entity['commercial_delivery']}")
                if entity.get("delivery_status"):
                    meta_bits.append(f"delivery_status={entity['delivery_status']}")
            meta = " / ".join(bit for bit in meta_bits if bit)
            lines.append(f"- {header}" + (f"（{meta}）" if meta else ""))
            for fact in entity.get("facts") or []:
                if not isinstance(fact, dict):
                    continue
                lines.append("  " + render_fact_line(fact))
    return "\n".join(lines) if lines else "（Fact Store 为空）"


def render_fact_line(fact: Dict[str, Any]) -> str:
    bits = [f"[{fact.get('id')}]",
            f"status={fact.get('status')}",
            f"scope={fact.get('scope') or 'unspecified'}"]
    if fact.get("numbers"):
        bits.append(f"numbers={list(fact['numbers'])}")
    if fact.get("jd_requirements"):
        bits.append(f"jd={list(fact['jd_requirements'])}")
    if fact.get("match_terms"):
        bits.append(f"denied_terms={list(fact['match_terms'])}")
    return f"{' '.join(bits)} — {fact.get('statement')}"


def render_capabilities(capabilities: Sequence[Dict[str, Any]]) -> str:
    if not capabilities:
        return "（无）"
    lines: List[str] = []
    for cap in capabilities:
        surfaces = "、".join(str(item) for item in (cap.get("surfaces") or []))
        facts = ", ".join(str(item) for item in (cap.get("evidence_facts") or []))
        weight = cap.get("weight", 2)
        lines.append(f"- {cap.get('label')}（capability={cap.get('capability')}, weight={weight}）")
        lines.append(f"  - 事实面：{surfaces}")
        lines.append(f"  - evidence facts：{facts}")
    return "\n".join(lines)


def render_pairwise_input(jd_text: str, ground_truth: Dict[str, Any],
                          source_resume: str, candidate_a: str, candidate_b: str,
                          context: str = "") -> str:
    """渲染 pairwise judge 的 user 消息。A/B 身份由调用方保证已随机化。"""
    parts: List[str] = []
    parts.append("# Target JD\n")
    parts.append(jd_text.strip())
    parts.append("\n# Source Facts (ground truth of what is true)\n")
    parts.append(render_fact_block(ground_truth))
    parts.append("\n# Source Resume (before optimization)\n")
    parts.append(source_resume.strip())
    if context.strip():
        parts.append("\n# Additional Relevant Context\n")
        parts.append(context.strip())
    parts.append("\n# Candidate A\n")
    parts.append(candidate_a.strip())
    parts.append("\n# Candidate B\n")
    parts.append(candidate_b.strip())
    parts.append("\n# Rubric\n")
    parts.append("Follow the system rubric exactly and return only the JSON object.")
    return "\n".join(parts)


def render_evidence_input(jd_text: str, ground_truth: Dict[str, Any],
                          resume_text: str, context: str = "") -> str:
    """渲染 JD Evidence Judge 的 user 消息。

    只给 JD + ground truth facts + 候选简历，不给 A/B 对比对象——
    这个 Judge 是逐份诊断，不参与胜负判定。
    """
    parts: List[str] = ["# Target JD\n", jd_text.strip(),
                        "\n# Source Facts (ground truth of what is true)\n",
                        render_fact_block(ground_truth)]
    if context.strip():
        parts.extend(["\n# Additional Relevant Context\n", context.strip()])
    parts.extend(["\n# Candidate Resume\n", resume_text.strip()])
    return "\n".join(parts)


def build_pairwise_payload(candidates: Dict[str, str],
                           dimensions: Sequence[str],
                           capabilities: Sequence[Dict[str, Any]],
                           ats_keywords: Sequence[str],
                           case_id: str) -> Dict[str, Any]:
    """传给 JudgeClient 的结构化 payload。

    真实模型只用渲染好的 prompt，忽略这里的细节；mock judge 直接读它。
    不含任何版本身份信息。
    """
    surfaces: List[str] = []
    for cap in capabilities:
        for surface in cap.get("surfaces") or []:
            surfaces.append(str(surface))
    return {
        "case_id": case_id,
        "candidates": dict(candidates),
        "dimensions": list(dimensions),
        # mock-only：能力表述面与 ATS 词表，用于近似覆盖度，不代表真实判断
        "mock_surfaces": surfaces,
        "mock_ats": [str(item) for item in ats_keywords],
    }
