#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Eval 公共结构定义与校验。

两件事：

1. benchmark case / pairwise judge 输出的**结构校验**（不依赖第三方库，缺失字段
   必须显式报错，不能默认通过）；
2. judge 返回文本的**结构化提取**（模型偶尔会带 markdown code fence 或前后说明，
   这里只做提取与规范化，不做语义补救）。

第三方的 `jsonschema` 只在显式做 JSON Schema 自检时使用，缺失则如实报告 skipped。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # pragma: no cover - 可选依赖
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover
    jsonschema = None

# --------------------------------------------------------------------------
# 结构定义
# --------------------------------------------------------------------------

WINNER_VALUES = ("A", "B", "Tie")

# Pairwise Judge 的评分维度。顺序即报告展示顺序。
# 这些维度只用于「哪一份更好」的比较，不参与事实安全判断——
# 事实安全是 Fact Gate，不在这里混成一个综合分。
PAIRWISE_DIMENSIONS: Tuple[str, ...] = (
    "jd_evidence_coverage",
    "evidence_strength",
    "recruiter_salience",
    "information_density",
    "redundancy_conciseness",
    "ats_terminology",
    "interview_defensibility",
)

DIMENSION_LABELS = {
    "jd_evidence_coverage": "JD Evidence Coverage",
    "evidence_strength": "Evidence Strength",
    "recruiter_salience": "Recruiter Salience",
    "information_density": "Information Density",
    "redundancy_conciseness": "Redundancy / Conciseness",
    "ats_terminology": "ATS Terminology Coverage",
    "interview_defensibility": "Interview Defensibility",
    "overall": "Overall Effectiveness",
}

CASE_REQUIRED_METADATA = ("role_family", "difficulty", "source")
CASE_REQUIRED_INPUTS = ("source_resume", "fact_store", "jd")
CASE_REQUIRED_EXPECTATIONS = ("must_preserve", "forbidden_claims", "important_capabilities")

VALID_ROLE_FAMILIES = (
    "ai-fde",
    "ai-delivery",
    "ai-solutions",
    "technical-consultant",
    "technical-support",
    "agent-product",
    "presales",
    "customer-success",
)
VALID_DIFFICULTY = ("easy", "medium", "hard")
VALID_SOURCE = ("real-world", "synthetic", "real-world-derived")


# --------------------------------------------------------------------------
# 校验
# --------------------------------------------------------------------------

def validate_benchmark_case(case: Any) -> List[str]:
    """返回问题列表，空列表表示通过。"""
    problems: List[str] = []
    case_id = getattr(case, "case_id", "?")

    for key in CASE_REQUIRED_METADATA:
        if not str(case.metadata.get(key) or "").strip():
            problems.append(f"{case_id}: metadata.{key} 缺失")

    family = str(case.metadata.get("role_family") or "")
    if family and family not in VALID_ROLE_FAMILIES:
        problems.append(f"{case_id}: metadata.role_family 不在允许值内：{family}")
    difficulty = str(case.metadata.get("difficulty") or "")
    if difficulty and difficulty not in VALID_DIFFICULTY:
        problems.append(f"{case_id}: metadata.difficulty 不在允许值内：{difficulty}")
    source = str(case.metadata.get("source") or "")
    if source and source not in VALID_SOURCE:
        problems.append(f"{case_id}: metadata.source 不在允许值内：{source}")

    for key in CASE_REQUIRED_INPUTS:
        if not case.inputs.get(key):
            problems.append(f"{case_id}: inputs.{key} 缺失")
            continue
        try:
            case.path_for(key)
        except Exception as exc:  # noqa: BLE001 - LoadError 之外也要暴露
            problems.append(f"{case_id}: inputs.{key} 路径无法解析（{exc}）")

    for key in CASE_REQUIRED_EXPECTATIONS:
        value = case.expectations.get(key)
        if value is None:
            problems.append(f"{case_id}: expectations.{key} 缺失")
        elif not isinstance(value, list):
            problems.append(f"{case_id}: expectations.{key} 必须是数组")

    # fact id 必须真实存在，否则 must_preserve 会静默失效
    if case.inputs.get("fact_store"):
        try:
            known = collect_fact_ids(case.fact_store())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{case_id}: fact_store 无法加载（{exc}）")
        else:
            for fact_id in case.must_preserve():
                if fact_id not in known:
                    problems.append(f"{case_id}: expectations.must_preserve 引用了不存在的 fact：{fact_id}")

    if case.human_review:
        winner = case.human_review.get("winner")
        if winner is not None and str(winner) not in WINNER_VALUES:
            problems.append(f"{case_id}: human_review.winner 必须是 A / B / Tie，实际 {winner}")

    return problems


def collect_fact_ids(store: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    for kind in ("experiences", "projects"):
        for entity in store.get(kind) or []:
            if isinstance(entity, dict):
                for fact in entity.get("facts") or []:
                    if isinstance(fact, dict) and fact.get("id"):
                        ids.append(str(fact["id"]))
    return ids


def validate_pairwise_result(data: Any, dimensions: Sequence[str] = PAIRWISE_DIMENSIONS,
                             require_overall: bool = True) -> List[str]:
    problems: List[str] = []
    if not isinstance(data, dict):
        return ["judge 输出不是 JSON object"]

    for dim in dimensions:
        entry = data.get(dim)
        if not isinstance(entry, dict):
            problems.append(f"缺少维度 {dim}")
            continue
        winner = entry.get("winner")
        if normalize_winner(winner) is None:
            problems.append(f"{dim}.winner 非法：{winner!r}")
        if not str(entry.get("reason") or "").strip():
            problems.append(f"{dim}.reason 为空")

    if require_overall:
        overall = data.get("overall")
        if not isinstance(overall, dict):
            problems.append("缺少 overall")
        else:
            if normalize_winner(overall.get("winner")) is None:
                problems.append(f"overall.winner 非法：{overall.get('winner')!r}")
            if not str(overall.get("reason") or "").strip():
                problems.append("overall.reason 为空")
            confidence = overall.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
                    or not 0.0 <= float(confidence) <= 1.0:
                problems.append(f"overall.confidence 必须是 0~1 的数字，实际 {confidence!r}")
    return problems


def normalize_winner(value: Any) -> Optional[str]:
    """把 judge 的各种写法归一到 A / B / Tie，无法识别时返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower().replace(" ", "")
    if lowered in ("a", "candidate_a", "resumea", "left"):
        return "A"
    if lowered in ("b", "candidate_b", "resumeb", "right"):
        return "B"
    if lowered in ("tie", "equal", "draw", "same", "平局", "相同", "打平", "无差异"):
        return "Tie"
    return None


JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中提取第一个 JSON object。

    先尝试整体解析，再尝试 code fence，最后退化为首尾花括号切片。
    只做提取，不做字段补全——缺字段交给 validate_pairwise_result 报错。
    """
    if not text:
        return None
    candidates: List[str] = [text.strip()]
    for match in JSON_FENCE_RE.finditer(text):
        candidates.append(match.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# --------------------------------------------------------------------------
# JSON Schema 自检（可选依赖）
# --------------------------------------------------------------------------

def json_schema_status() -> str:
    return "available" if jsonschema is not None else "unavailable"


def validate_json_schema(instance: Any, schema_path: Any, label: str = "") -> List[str]:
    """用 JSON Schema 校验单个实例；jsonschema 缺失时返回空列表并说明由调用方处理。"""
    if jsonschema is None:
        return []
    with open(schema_path, "r", encoding="utf-8") as handle:
        schema = json.load(handle)
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:  # type: ignore
        location = "/".join(str(part) for part in exc.absolute_path)
        return [f"{label or '<instance>'}: {exc.message} @ {location}"]
    return []
