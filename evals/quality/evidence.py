#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JD Evidence Recall（deterministic 版本）。

核心定义：

    最终简历成功表达出的高价值真实 Evidence
    ÷
    Fact Store 中实际可用于该 JD 的高价值 Evidence

关键约束：**分母只包含候选人真实拥有的 evidence**。
如果 Fact Store 里根本没有 Kubernetes 经验，那么简历没写 Kubernetes 不扣分——
这里测的是「现有真实证据有没有被最大化利用」，不是「有没有凭空变得和 JD 一样」。

高价值 evidence 的权重来自 benchmark case 的 `important_capabilities[].weight`，
而不是简单按 fact 数量计数。

`must_preserve` 是 must 子集，单独统计命中情况：它的失败非常直接地说明
「版本把该岗位最该展示的证据弄丢了」。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import validate_claims as vc  # noqa: E402

USABLE_STATUSES = ("confirmed", "transferable")
BULLET_SIMILARITY_THRESHOLD = 0.45
CLAIM_SIMILARITY_THRESHOLD = 0.6


@dataclass
class EvidenceItem:
    fact_id: str
    weight: int
    capability: str
    statement: str
    status: str
    used: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact": self.fact_id,
            "weight": self.weight,
            "capability": self.capability,
            "statement": self.statement,
            "status": self.status,
            "used": self.used,
        }


@dataclass
class EvidenceRecall:
    available: List[EvidenceItem] = field(default_factory=list)
    recall: Optional[float] = None
    must_preserve_hits: List[str] = field(default_factory=list)
    must_preserve_missed: List[str] = field(default_factory=list)
    based_on_claims: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def available_weight(self) -> int:
        return sum(item.weight for item in self.available)

    @property
    def used_weight(self) -> int:
        return sum(item.weight for item in self.available if item.used)

    @property
    def used_facts(self) -> List[str]:
        return [item.fact_id for item in self.available if item.used]

    @property
    def missed_high_value(self) -> List[Dict[str, Any]]:
        return [{"fact": item.fact_id, "weight": item.weight,
                 "capability": item.capability}
                for item in self.available if not item.used]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "available_relevant_evidence": len(self.available),
            "used_relevant_evidence": len(self.used_facts),
            "available_weight": self.available_weight,
            "used_weight": self.used_weight,
            "recall": self.recall,
            "missed_high_value_evidence": self.missed_high_value,
            "must_preserve_hits": self.must_preserve_hits,
            "must_preserve_missed": self.must_preserve_missed,
            "based_on_claims": self.based_on_claims,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------
# fact 索引
# --------------------------------------------------------------------------

def index_facts(store: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for kind in ("experiences", "projects"):
        for entity in store.get(kind) or []:
            if not isinstance(entity, dict):
                continue
            for fact in entity.get("facts") or []:
                if isinstance(fact, dict) and fact.get("id"):
                    out[str(fact["id"])] = fact
    return out


# --------------------------------------------------------------------------
# used 判定
# --------------------------------------------------------------------------

def visible_fact_ids_from_claims(store: Optional[Dict[str, Any]],
                                 resume_text: str) -> Optional[set]:
    """从该版本的 claims 侧车判断哪些 fact 的表述真的出现在简历里。

    返回 None 表示没有可用 claims（调用方退化为文本匹配）。
    """
    if not isinstance(store, dict):
        return None
    claims = store.get("claims")
    if not isinstance(claims, list) or not claims:
        return None
    normalized_resume = vc.normalize(resume_text or "")
    used: set = set()
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        plain = vc.strip_markup(str(claim.get("text") or ""))
        normalized_claim = vc.normalize(plain)
        if not normalized_claim:
            continue
        visible = normalized_claim in normalized_resume
        if not visible:
            visible = any(
                vc.similarity(plain, bullet) >= CLAIM_SIMILARITY_THRESHOLD
                for bullet in vc.resume_bullets(resume_text or ""))
        if not visible:
            continue
        for ref in claim.get("evidence") or []:
            used.add(str(ref))
    return used


def fact_visible_by_text(fact: Dict[str, Any], resume_text: str) -> bool:
    """没有 claims 时的退化判定：fact 陈述与简历 bullet 的相似度。"""
    statement = str(fact.get("statement") or "")
    if not statement:
        return False
    normalized_resume = vc.normalize(resume_text or "")
    if vc.normalize(statement) and vc.normalize(statement) in normalized_resume:
        return True
    return any(vc.similarity(statement, bullet) >= BULLET_SIMILARITY_THRESHOLD
               for bullet in vc.resume_bullets(resume_text or ""))


# --------------------------------------------------------------------------
# 计算
# --------------------------------------------------------------------------

def compute_evidence_recall(ground_truth: Dict[str, Any],
                            resume_text: str,
                            capabilities: Sequence[Dict[str, Any]],
                            must_preserve: Sequence[str] = (),
                            run_store: Optional[Dict[str, Any]] = None) -> EvidenceRecall:
    result = EvidenceRecall()
    facts = index_facts(ground_truth)

    best_weight: Dict[str, int] = {}
    capability_of: Dict[str, str] = {}
    for cap in capabilities:
        weight = int(cap.get("weight") or 2)
        name = str(cap.get("capability") or "")
        for fact_id in cap.get("evidence_facts") or []:
            fact_id = str(fact_id)
            fact = facts.get(fact_id)
            if fact is None:
                continue
            if str(fact.get("status")) not in USABLE_STATUSES:
                # denied / unknown 的事实不能算「可用 evidence」，不计入分母。
                continue
            if weight > best_weight.get(fact_id, 0):
                best_weight[fact_id] = weight
                capability_of[fact_id] = name

    if not best_weight:
        result.notes.append("该 case 没有声明可用的高价值 evidence，Evidence Recall = n/a")
        return result

    claim_used = visible_fact_ids_from_claims(run_store, resume_text)
    result.based_on_claims = claim_used is not None
    if claim_used is None:
        result.notes.append(
            "未提供该版本的 claims 侧车，Evidence Recall 使用 fact 陈述与简历 bullet "
            "的相似度近似判定（degraded）。")

    for fact_id, weight in sorted(best_weight.items(), key=lambda item: (-item[1], item[0])):
        fact = facts[fact_id]
        if claim_used is not None:
            used = fact_id in claim_used
        else:
            used = fact_visible_by_text(fact, resume_text)
        result.available.append(EvidenceItem(
            fact_id=fact_id, weight=weight,
            capability=capability_of.get(fact_id, ""),
            statement=str(fact.get("statement") or ""),
            status=str(fact.get("status") or ""),
            used=used))

    if result.available_weight:
        result.recall = round(result.used_weight / result.available_weight, 4)

    available_ids = {item.fact_id for item in result.available}
    used_ids = set(result.used_facts)
    for fact_id in must_preserve:
        fact_id = str(fact_id)
        if fact_id not in available_ids:
            # must_preserve 指向的 fact 不在本次可用集合内（例如状态被改为 unknown）。
            continue
        if fact_id in used_ids:
            result.must_preserve_hits.append(fact_id)
        else:
            result.must_preserve_missed.append(fact_id)
    return result
