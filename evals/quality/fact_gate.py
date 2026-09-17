#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fact Safety Hard Gate。

这是 Quality Eval 的第一道门，也是唯一的**否决权**：事实安全不是评分维度，
不能被表达质量抵消。

    if candidate.fact_gate == FAIL:
        candidate cannot win quality comparison

Gate 分为两层：

1. deterministic（默认开启）
   直接复用 `scripts/validate_claims.py`，只把属于事实安全类别的 error 级 issue
   作为否决项。可代码判断的事情不用 LLM 判断。

2. semantic（可选，`--semantic-fact-guard`）
   用 `judges/fact_guard.md` 补 deterministic 抓不到的同义改写、语义级 scope 夸大、
   项目边界越界。它只能**增加**否决项，不能把 deterministic 的失败改判为通过。

未被纳入 Gate 的错误类别（例如 `JD_CORE_REQUIREMENT_UNCOVERED`）属于内容质量问题，
会在回归评测里拦截，但不构成事实安全违规。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import validate_claims as vc  # noqa: E402

# --------------------------------------------------------------------------
# Gate 分类
# --------------------------------------------------------------------------

GATE_CATEGORY_BY_CODE: Dict[str, str] = {
    # 无事实支撑的断言
    "CLAIM_MISSING_FACT_REF": "unsupported_claim",
    "CLAIM_WITHOUT_EVIDENCE": "unsupported_claim",
    "CLAIM_REFERENCES_UNKNOWN_FACT": "unsupported_claim",
    # 简历里出现带数字的 bullet 却没有对应 claim 记录。
    # validate_claims 默认把它当 warning（属于卫生问题），但它的触发条件只可能是数字，
    # 语义上就是「没有 evidence 的数字」——即虚构指标，因此 Fact Gate 必须否决。
    # 这与 Skill 自身契约一致：SKILL.md 步骤 6 要求每条事实性 bullet 登记 claim。
    "RESUME_BULLET_UNVERIFIED": "fabricated_metric",
    # 虚构指标
    "UNSUPPORTED_NUMBER": "fabricated_metric",
    # scope 越界
    "SCOPE_INFLATION": "scope_inflation",
    # 项目边界
    "PROJECT_BOUNDARY_VIOLATION": "project_boundary",
    "PROJECT_TYPE_MISMATCH": "project_boundary",
    "PROJECT_DELIVERY_MISMATCH": "project_boundary",
    # 已否认事实复活
    "CLAIM_REFERENCES_DENIED_FACT": "denied_fact",
    "DENIED_TERM_REAPPEARS": "denied_fact",
    # 可迁移被写成直接经验
    "TRANSFERABLE_AS_DIRECT": "transferable_as_direct",
    # 交付卫生：残留占位符同样属于「不能交付」，非表达质量问题
    "RESUME_PLACEHOLDER": "output_hygiene",
}

# --strict-gate 时额外升级为否决项的 warning 级代码。
# 默认关闭：这些是「需要先确认」的信号，不是已确认的违规。
STRICT_EXTRA_CODES: Dict[str, str] = {
    "SCOPE_OVERREACH": "scope_inflation",
    "SCOPE_UNVERIFIABLE": "scope_inflation",
    "PROJECT_COMMERCIAL_UNVERIFIED": "project_boundary",
}

# Gate 违规类别清单。报告的 Fact Safety 段落按这些类别聚合，新增类别时同步更新。
GATE_CATEGORIES = (
    "unsupported_claim",
    "fabricated_metric",
    "scope_inflation",
    "project_boundary",
    "denied_fact",
    "transferable_as_direct",
    "output_hygiene",
    "semantic_fact_guard",
)


@dataclass
class GateViolation:
    code: str
    category: str
    severity: str
    message: str
    location: str = ""
    source: str = "deterministic"

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
            "source": self.source,
        }


@dataclass
class GateResult:
    passed: bool = True
    scope: str = "full"  # full | resume_only
    violations: List[GateViolation] = field(default_factory=list)
    warnings: List[GateViolation] = field(default_factory=list)
    forbidden_hits: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "scope": self.scope,
            "violations": [v.to_dict() for v in self.violations],
            "gate_warnings": [v.to_dict() for v in self.warnings],
            "forbidden_hits": self.forbidden_hits,
            "notes": self.notes,
        }

    def categories(self) -> List[str]:
        return sorted({v.category for v in self.violations})


# --------------------------------------------------------------------------
# deterministic 层
# --------------------------------------------------------------------------

def evaluate_deterministic(store: Optional[Dict[str, Any]], resume_text: str,
                           strict: bool = False) -> GateResult:
    """对单个版本跑确定性 Fact Gate。

    `store` 是该版本自己的 fact store（含 claims）。缺失时退化为简历级检查，
    并在 `scope` / `notes` 中如实标注，不假装做过完整校验。
    """
    result = GateResult()

    if not isinstance(store, dict):
        result.scope = "resume_only"
        result.notes.append(
            "未提供该版本的 facts sidecar，Fact Gate 退化为简历级检查"
            "（占位符 / denied 关键词 / 禁写项），claims 级校验未执行。")

    checked_store = store if isinstance(store, dict) else {
        "meta": {"candidate": "unknown"}, "claims": [],
    }
    try:
        issues = vc.validate(checked_store, resume_text)
    except Exception as exc:  # noqa: BLE001 - 校验器异常不能静默变成通过
        result.passed = False
        result.violations.append(GateViolation(
            code="GATE_EXECUTION_ERROR", category="unsupported_claim",
            severity="error", message=f"validate_claims 执行异常: {exc}"))
        return result

    for issue in issues:
        # 被声明进 GATE_CATEGORY_BY_CODE 的 issue code 就是否决项：
        # 是否否决由 Fact Gate 决定，不再沿用 validate_claims 的默认 severity
        # （它面向 lint / 人读，默认 severity 更保守）。
        category = GATE_CATEGORY_BY_CODE.get(issue.code)
        if category is not None:
            result.violations.append(GateViolation(
                code=issue.code, category=category, severity="error",
                message=issue.message, location=issue.location,
                source="deterministic"))
            continue
        # --strict-gate 时，把「需先确认」的 warning 也升级为否决项。
        strict_category = STRICT_EXTRA_CODES.get(issue.code) if strict else None
        if strict_category is not None:
            result.violations.append(GateViolation(
                code=issue.code, category=strict_category, severity="error",
                message=issue.message, location=issue.location,
                source="deterministic(strict)"))
            continue
        if issue.severity != "error":
            # 其余 warning：记录为观察项，不参与否决。
            result.warnings.append(GateViolation(
                code=issue.code, category="observation", severity=issue.severity,
                message=issue.message, location=issue.location,
                source="deterministic"))
        # 其余 error 级但非事实安全类别（如 JD_CORE_REQUIREMENT_UNCOVERED）：
        # 属于内容质量问题，由 Regression Eval 拦截，不在此否决。

    result.passed = not result.violations
    return result


# --------------------------------------------------------------------------
# 禁写项（diagnostic）
# --------------------------------------------------------------------------

def tokenize(text: str) -> List[str]:
    """中文按 2-gram，英文/数字按词切分，用于禁写项的模糊命中判断。"""
    import re as _re
    tokens: List[str] = []
    for word in _re.findall(r"[A-Za-z][A-Za-z0-9.+#/-]*", text or ""):
        tokens.append(word.lower())
    for chunk in _re.findall(r"[\u4e00-\u9fff]+", text or ""):
        if len(chunk) == 1:
            tokens.append(chunk)
            continue
        for i in range(len(chunk) - 1):
            tokens.append(chunk[i:i + 2])
    return tokens


def find_forbidden_hits(resume_text: str, forbidden_claims: Sequence[str],
                        threshold: float = 0.8) -> List[Dict[str, str]]:
    """在简历中找出疑似写出了禁写项的位置。

    这是**疑似命中**信号，不是 Gate 输入：中文表述天然可以同义改写，逐字或
    token 覆盖率匹配都会误报。用它做定位提示，最终判定交给 fact guard / 人工。
    """
    hits: List[Dict[str, str]] = []
    if not resume_text:
        return hits
    lines = [line.strip() for line in resume_text.splitlines() if line.strip()]
    for claim in forbidden_claims:
        target = set(tokenize(claim))
        if not target:
            continue
        best_ratio = 0.0
        best_line = ""
        for line in lines:
            if len(line) < 4:
                continue
            line_tokens = set(tokenize(line))
            if not line_tokens:
                continue
            overlap = len(target & line_tokens) / float(len(target))
            if overlap > best_ratio:
                best_ratio, best_line = overlap, line
        if best_ratio >= threshold:
            hits.append({
                "forbidden_claim": claim,
                "matched_line": best_line[:80],
                "token_overlap": f"{best_ratio:.2f}",
                "verdict": "疑似命中（需 fact guard / 人工确认）",
            })
    return hits


def scan_ground_truth_denied(ground_truth: Dict[str, Any],
                             resume_text: str) -> List[GateViolation]:
    """用 benchmark 的 ground truth 再兜一层 denied 关键词检查。

    即使某个版本没有提供 facts sidecar，也能发现 denied 事实被写回来的情况。
    """
    violations: List[GateViolation] = []
    for kind in ("experiences", "projects"):
        for entity in ground_truth.get(kind) or []:
            if not isinstance(entity, dict):
                continue
            for fact in entity.get("facts") or []:
                if not isinstance(fact, dict) or fact.get("status") != "denied":
                    continue
                for term in fact.get("match_terms") or []:
                    if str(term) and str(term) in (resume_text or ""):
                        violations.append(GateViolation(
                            code="DENIED_TERM_REAPPEARS",
                            category="denied_fact", severity="error",
                            message=(f"ground truth 中已否认事实 {fact.get('id')} 的"
                                     f"关键词重新出现：{term}"),
                            location=str(fact.get("id")),
                            source="ground_truth"))
    return violations


# --------------------------------------------------------------------------
# 汇总入口
# --------------------------------------------------------------------------

def evaluate(store: Optional[Dict[str, Any]], resume_text: str,
             ground_truth: Optional[Dict[str, Any]] = None,
             forbidden_claims: Sequence[str] = (),
             strict: bool = False) -> GateResult:
    result = evaluate_deterministic(store, resume_text, strict=strict)

    if ground_truth:
        result.violations.extend(scan_ground_truth_denied(ground_truth, resume_text))
        result.passed = not result.violations

    hits = find_forbidden_hits(resume_text, forbidden_claims)
    if hits:
        result.forbidden_hits = hits
        result.notes.append(
            f"{len(hits)} 条禁写项疑似命中，已记录为诊断信号（未计入 Gate）。")
    return result


def apply_semantic_result(result: GateResult, data: Dict[str, Any],
                          model: str = "") -> GateResult:
    """把 fact guard prompt 的结构化输出并入 Gate。

    语义层只能**增加**否决项：它返回 pass=true 不会把 deterministic 的失败翻转。
    """
    if not isinstance(data, dict):
        result.notes.append("fact guard 返回非结构化结果，已忽略（未计入 Gate）。")
        return result

    categories = (
        ("unsupported_claims", "unsupported_claim"),
        ("fabricated_metrics", "fabricated_metric"),
        ("scope_inflations", "scope_inflation"),
        ("project_boundary_violations", "project_boundary"),
        ("denied_facts_reintroduced", "denied_fact"),
        ("unknown_facts_asserted", "unsupported_claim"),
        ("transferable_as_direct", "transferable_as_direct"),
    )
    for key, category in categories:
        for finding in data.get(key) or []:
            if not isinstance(finding, dict):
                continue
            quote = str(finding.get("quote") or "")[:120]
            why = str(finding.get("why") or "")
            result.violations.append(GateViolation(
                code=f"SEMANTIC_{category.upper()}",
                category="semantic_fact_guard",
                severity="error",
                message=f"[{category}] {why} | 引用: {quote}",
                location=str(finding.get("fact_ref") or ""),
                source=f"semantic_fact_guard{':' + model if model else ''}"))
    if data.get("notes"):
        result.notes.append(f"fact guard: {str(data['notes'])[:160]}")
    result.passed = not result.violations
    return result


def build_fact_guard_input(ground_truth: Dict[str, Any], source_resume: str,
                           candidate_resume: str) -> Tuple[str, Dict[str, Any]]:
    """构造 fact guard 的 (user, payload)。

    system 由调用方用 prompt 文件正文填充；这里只负责 user 与结构化 payload。
    注意：只给 ground truth 事实 + 源简历 + 候选简历，不给任何生成过程信息。
    """
    from .render import render_fact_block  # 避免循环引用，延迟导入

    user = "\n".join([
        "# Source Facts (ground truth)",
        render_fact_block(ground_truth),
        "",
        "# Source Resume",
        source_resume.strip(),
        "",
        "# Candidate Resume To Audit",
        candidate_resume.strip(),
    ])
    payload = {
        "ground_truth": ground_truth,
        "source_resume": source_resume,
        "candidate_resume": candidate_resume,
    }
    return user, payload
