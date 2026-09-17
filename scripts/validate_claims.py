#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""optimize-resume 事实安全检查（deterministic）。

只处理适合代码判断的部分：

    - denied / unknown fact 是否通过 claim 重新回到简历
    - claim 引用了不存在的 fact id
    - claim 中出现 evidence 未支持的数字
    - 个人项目 / Demo 被表述为公司项目或商业交付
    - scope 被升级（support -> 主导 / 独立负责）
    - transferable 被当成直接经验
    - 重复 bullet / 重复 claim
    - placeholder / TODO 残留
    - claim 文本与简历文本漂移
    - JD 核心要求与 ATS 关键词覆盖（事实支持但简历缺失 / 简历出现但事实不支持）

语义判断（是否过度包装、是否自然、是否真的回应 JD、是否值得保留）仍然由 Agent
reasoning 负责。不要试图用正则解决语义问题。

用法:
    python3 scripts/validate_claims.py ./张三-AI解决方案工程师.facts.yaml
    python3 scripts/validate_claims.py store.yaml --resume ./张三-AI解决方案工程师.md
    python3 scripts/validate_claims.py store.yaml --resume resume.md --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

FACT_STATUSES = ("confirmed", "denied", "unknown", "transferable")
FACT_SOURCES = ("original_resume", "user_confirmation", "user_correction", "user_note", "inferred")

SCOPE_LEVELS = {"support": 0, "core_execution": 1, "lead": 2, "owner": 3}

# claim 动词 -> scope 级别。级别高的先匹配。
# 只收录真正表达“谁承担什么”的动词；"上线" / "交付" 一类活动词不体现 scope，不收录。
SCOPE_VERBS: Sequence[Tuple[str, Sequence[str]]] = (
    ("owner", ("独立负责", "独立设计", "独立搭建", "独立完成", "独立交付", "全权负责",
               "从 0 到 1", "从0到1", "从零到一", "从零搭建")),
    ("lead", ("主导", "牵头", "统筹", "带领团队", "带领", "组织跨团队", "推动跨团队",
              "端到端负责", "整体负责", "总体规划")),
    ("core_execution", ("负责", "承担", "实现", "集成", "落地", "定位", "编写", "开发",
                        "设计", "搭建", "推进", "重构", "调优")),
    ("support", ("支持", "协助", "跟进", "维护", "配合", "参与", "辅助")),
)

# 扫描 scope 动词前先剥离的名词（避免 "技术支持" / "项目负责人" 被当成动词）
SCOPE_NOUN_STOPWORDS = ("技术支持", "售后支持", "客户支持", "支持团队", "支持岗位",
                        "负责人", "负责制")

NON_WORK_PROJECT_TYPES = ("personal_project", "demo", "open_source")

COMMERCIAL_TERMS = ("商业化", "商业交付", "商业价值", "企业客户", "客户上线", "客户成功",
                    "付费", "合同", "营收", "续约", "客户签约", "服务企业", "对外交付",
                    "客户满意度")

# 用于识别“非工作项目被表述为公司项目”。刻意不用裸的“公司”，
# 否则“负责公司内部工单系统维护”会被误报。
COMPANY_SCOPE_TERMS = ("公司级", "公司项目", "公司平台", "所在公司", "企业级平台",
                       "公司产品线", "公司自研")

DELIVERY_TERMS = ("上线", "已交付", "完成交付", "投产", "验收通过")

MIGRATION_MARKERS = ("可迁移", "迁移至", "迁移到", "可复用于", "适用于", "底层能力",
                     "可延展", "可复用")

PLACEHOLDER_PATTERNS = (r"TODO", r"TBD", r"XXX", r"<!--", r"-->", r"\{\{", r"\}\}",
                        r"待补充", r"待确认", r"待填写", r"【待")

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
DATE_LIKE_RE = re.compile(r"(?:19|20)\d{2}\s*[-/年.]\s*\d{1,2}(?:\s*[-/月.]\s*\d{1,2})?")

METRIC_BY_CODE = {
    "STORE_SCHEMA_INVALID": "fact_fidelity",
    "STORE_DUPLICATE_ID": "fact_fidelity",
    "CLAIM_MISSING_FACT_REF": "fact_fidelity",
    "CLAIM_REFERENCES_DENIED_FACT": "fact_fidelity",
    "CLAIM_REFERENCES_UNKNOWN_FACT": "fact_fidelity",
    "DENIED_TERM_REAPPEARS": "fact_fidelity",
    "UNSUPPORTED_NUMBER": "unsupported_claim_rate",
    "CLAIM_WITHOUT_EVIDENCE": "unsupported_claim_rate",
    "SCOPE_INFLATION": "scope_inflation_rate",
    "SCOPE_OVERREACH": "scope_inflation_rate",
    "SCOPE_UNVERIFIABLE": "scope_inflation_rate",
    "TRANSFERABLE_AS_DIRECT": "transferable_discipline",
    "PROJECT_BOUNDARY_VIOLATION": "project_boundary_integrity",
    "PROJECT_COMMERCIAL_UNVERIFIED": "project_boundary_integrity",
    "PROJECT_TYPE_MISMATCH": "project_boundary_integrity",
    "PROJECT_DELIVERY_MISMATCH": "project_boundary_integrity",
    "RESUME_PLACEHOLDER": "output_hygiene",
    "RESUME_DUPLICATE_BULLET": "duplicate_information_rate",
    "CLAIM_DUPLICATE_TEXT": "duplicate_information_rate",
    "RESUME_BULLET_UNVERIFIED": "fact_fidelity",
    "RESUME_CLAIM_TEXT_MISMATCH": "fact_fidelity",
    "JD_CORE_REQUIREMENT_UNCOVERED": "jd_core_coverage",
    "JD_REQUIREMENT_NO_EVIDENCE": "jd_core_coverage",
    "ATS_KEYWORD_MISSING": "ats_keyword_coverage",
    "ATS_KEYWORD_UNSUPPORTED": "ats_keyword_coverage",
    "RECRUITER_SALIENCE_MISSING": "recruiter_salience",
}

DEFAULT_SEVERITY: Dict[str, str] = {
    "STORE_SCHEMA_INVALID": "error",
    "STORE_DUPLICATE_ID": "error",
    "CLAIM_MISSING_FACT_REF": "error",
    "CLAIM_REFERENCES_DENIED_FACT": "error",
    "CLAIM_REFERENCES_UNKNOWN_FACT": "error",
    "DENIED_TERM_REAPPEARS": "error",
    "UNSUPPORTED_NUMBER": "error",
    "CLAIM_WITHOUT_EVIDENCE": "error",
    "SCOPE_INFLATION": "error",
    "TRANSFERABLE_AS_DIRECT": "error",
    "PROJECT_BOUNDARY_VIOLATION": "error",
    "PROJECT_TYPE_MISMATCH": "error",
    "PROJECT_COMMERCIAL_UNVERIFIED": "warning",
    "PROJECT_DELIVERY_MISMATCH": "error",
    "RESUME_PLACEHOLDER": "error",
    "JD_CORE_REQUIREMENT_UNCOVERED": "error",
    "SCOPE_OVERREACH": "warning",
    "SCOPE_UNVERIFIABLE": "warning",
    "RESUME_DUPLICATE_BULLET": "warning",
    "CLAIM_DUPLICATE_TEXT": "warning",
    "RESUME_BULLET_UNVERIFIED": "warning",
    "RESUME_CLAIM_TEXT_MISMATCH": "warning",
    "JD_REQUIREMENT_NO_EVIDENCE": "warning",
    "ATS_KEYWORD_MISSING": "warning",
    "ATS_KEYWORD_UNSUPPORTED": "warning",
    "RECRUITER_SALIENCE_MISSING": "warning",
}

# Recruiter Salience 的“前段可见区”兜底比例：无法解析章节时使用。
FRONT_SECTION_RATIO = 0.5
DUPLICATE_SIMILARITY = 0.72
BULLET_MATCH_SIMILARITY = 0.5


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

class Issue:
    __slots__ = ("code", "severity", "message", "location")

    def __init__(self, code: str, message: str, location: str = "") -> None:
        self.code = code
        self.severity = DEFAULT_SEVERITY.get(code, "warning")
        self.message = message
        self.location = location

    def to_dict(self) -> Dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
            "metric": METRIC_BY_CODE.get(self.code, ""),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Issue {self.severity} {self.code} {self.message}>"


# --------------------------------------------------------------------------
# 文本工具
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def strip_markup(text: str) -> str:
    """去掉 **关键字**：前缀与 markdown 标记，保留正文。"""
    text = re.sub(r"\*\*([^*]*)\*\*：?", r"\1 ", str(text or ""))
    text = text.replace("**", " ")
    return re.sub(r"\s+", " ", text).strip()


def extract_numbers(text: str) -> Set[str]:
    cleaned = DATE_LIKE_RE.sub(" ", str(text or ""))
    out: Set[str] = set()
    for match in NUMBER_RE.finditer(cleaned):
        token = match.group(0)
        if len(token) == 4 and token.isdigit() and 1900 <= int(token) <= 2099:
            continue
        out.add(canonical_number(token))
    return out


def canonical_number(token: str) -> str:
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token or "0"


def similarity(left: str, right: str) -> float:
    """字符 bigram Jaccard，用于中文近重复判断。"""
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ga = {a[i:i + 2] for i in range(len(a) - 1)}
    gb = {b[i:i + 2] for i in range(len(b) - 1)}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / float(len(ga | gb))


def detect_scope(text: str) -> Tuple[Optional[str], Optional[str]]:
    """返回 claim 文本中出现的最高 scope 级别及其触发词。"""
    cleaned = str(text or "")
    for noun in SCOPE_NOUN_STOPWORDS:
        cleaned = cleaned.replace(noun, " ")
    for level_name, verbs in SCOPE_VERBS:
        for verb in verbs:
            if verb in cleaned:
                return level_name, verb
    return None, None


def contains_any(text: str, terms: Iterable[str]) -> Optional[str]:
    for term in terms:
        if term and term in text:
            return term
    return None


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------

def load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("需要 PyYAML：python3 -m pip install pyyaml")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_store(path: Path) -> Dict[str, Any]:
    data = load_yaml(path)
    if not isinstance(data, dict):
        raise ValueError(f"fact store 必须是 YAML mapping: {path}")
    return data


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------

class StoreIndex:
    def __init__(self, store: Dict[str, Any]) -> None:
        self.store = store
        self.facts: Dict[str, Dict[str, Any]] = {}
        self.fact_owner: Dict[str, Dict[str, Any]] = {}
        self.fact_owner_kind: Dict[str, str] = {}
        self.claims: List[Dict[str, Any]] = []
        self.jd: Dict[str, Any] = store.get("jd") or {}

    def build(self, issues: List[Issue]) -> "StoreIndex":
        for kind in ("experiences", "projects"):
            for entity in self.store.get(kind) or []:
                if not isinstance(entity, dict):
                    continue
                for fact in entity.get("facts") or []:
                    if not isinstance(fact, dict):
                        continue
                    fid = str(fact.get("id") or "").strip()
                    if not fid:
                        issues.append(Issue("STORE_SCHEMA_INVALID",
                                            f"{kind} 下存在缺少 id 的 fact", str(entity.get("id", ""))))
                        continue
                    if fid in self.facts:
                        issues.append(Issue("STORE_DUPLICATE_ID",
                                            f"fact id 重复：{fid}", fid))
                        continue
                    self.facts[fid] = fact
                    self.fact_owner[fid] = entity
                    self.fact_owner_kind[fid] = kind

        seen_claim_ids: Set[str] = set()
        for claim in self.store.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            cid = str(claim.get("id") or "").strip()
            if cid and cid in seen_claim_ids:
                issues.append(Issue("STORE_DUPLICATE_ID",
                                    f"claim id 重复：{cid}", cid))
                continue
            seen_claim_ids.add(cid)
            self.claims.append(claim)
        return self

    def evidence_facts(self, claim: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = []
        for ref in claim.get("evidence") or []:
            fact = self.facts.get(str(ref))
            if fact is not None:
                out.append(fact)
        return out

    def fact_numbers(self, fact: Dict[str, Any]) -> Set[str]:
        declared = fact.get("numbers")
        if isinstance(declared, list) and declared:
            return {canonical_number(str(item)) for item in declared}
        return extract_numbers(fact.get("statement", ""))

    def all_fact_text(self) -> str:
        parts = []
        for fact in self.facts.values():
            parts.append(str(fact.get("statement", "")))
            for term in fact.get("match_terms") or []:
                parts.append(str(term))
        return "\n".join(parts)

    def requirement_terms(self) -> List[str]:
        terms = self.jd.get("core_requirements") or []
        return [str(item) for item in terms if str(item).strip()]

    def keyword_terms(self) -> List[str]:
        terms = self.jd.get("ats_keywords") or []
        return [str(item) for item in terms if str(item).strip()]


# --------------------------------------------------------------------------
# 检查：结构
# --------------------------------------------------------------------------

def check_structure(store: Dict[str, Any]) -> List[Issue]:
    issues: List[Issue] = []
    if not isinstance(store.get("meta"), dict):
        issues.append(Issue("STORE_SCHEMA_INVALID", "缺少 meta 段"))
    elif not str(store["meta"].get("candidate") or "").strip():
        issues.append(Issue("STORE_SCHEMA_INVALID", "meta.candidate 不能为空"))
    if not isinstance(store.get("claims"), list):
        issues.append(Issue("STORE_SCHEMA_INVALID", "缺少 claims 段（可为空数组）"))
    for kind in ("experiences", "projects"):
        value = store.get(kind)
        if value is not None and not isinstance(value, list):
            issues.append(Issue("STORE_SCHEMA_INVALID", f"{kind} 必须是数组"))
    return issues


# --------------------------------------------------------------------------
# 检查：claim 与 fact
# --------------------------------------------------------------------------

def check_claims(index: StoreIndex) -> List[Issue]:
    issues: List[Issue] = []
    seen_texts: List[Tuple[str, str]] = []

    for claim in index.claims:
        cid = str(claim.get("id") or "?")
        text = str(claim.get("text") or "")
        plain = strip_markup(text)
        refs = [str(item) for item in (claim.get("evidence") or [])]
        claim_type = str(claim.get("claim_type") or "direct")

        # 1. 引用了不存在的 fact id
        for ref in refs:
            if ref not in index.facts:
                issues.append(Issue("CLAIM_MISSING_FACT_REF",
                                    f"claim 引用了不存在的 fact id：{ref}", cid))

        facts = index.evidence_facts(claim)

        # 2. denied / unknown fact 不得被引用
        denied_facts = [f for f in facts if f.get("status") == "denied"]
        for fact in denied_facts:
            issues.append(Issue("CLAIM_REFERENCES_DENIED_FACT",
                                f"claim 引用了已否认事实 {fact.get('id')}：{fact.get('statement')}", cid))
        for fact in facts:
            if fact.get("status") == "unknown":
                issues.append(Issue("CLAIM_REFERENCES_UNKNOWN_FACT",
                                    f"claim 引用了未知事实 {fact.get('id')}：{fact.get('statement')}", cid))

        # 3. denied fact 的 match_terms 不得在 claim 文本中重新出现
        for fact in index.facts.values():
            if fact.get("status") != "denied":
                continue
            term = contains_any(plain, [str(t) for t in (fact.get("match_terms") or [])])
            if term:
                issues.append(Issue("DENIED_TERM_REAPPEARS",
                                    f"已否认事实 {fact.get('id')} 的关键词重新出现：{term}", cid))

        # 4. 数字必须有 evidence 支持
        evidence_numbers: Set[str] = set()
        for fact in facts:
            evidence_numbers |= index.fact_numbers(fact)
        claim_numbers = claim.get("numbers")
        if isinstance(claim_numbers, list) and claim_numbers:
            claim_numbers = {canonical_number(str(item)) for item in claim_numbers}
        else:
            claim_numbers = extract_numbers(plain)
        for number in sorted(claim_numbers - evidence_numbers):
            issues.append(Issue("UNSUPPORTED_NUMBER",
                                f"数字 {number} 不在 evidence 支持范围内", cid))

        # 5. 事实性 claim 必须有 evidence
        level_name, verb = detect_scope(plain)
        factual = bool(claim_numbers) or (level_name is not None and level_name != "support")
        if not facts and factual:
            trigger = f"数字 {sorted(claim_numbers)}" if claim_numbers else f"scope 动词「{verb}」"
            issues.append(Issue("CLAIM_WITHOUT_EVIDENCE",
                                f"含事实性内容（{trigger}）但没有 evidence", cid))

        # 6. transferable 不得写成直接经验
        if facts and claim_type == "direct":
            statuses = {str(f.get("status")) for f in facts}
            if statuses == {"transferable"}:
                issues.append(Issue("TRANSFERABLE_AS_DIRECT",
                                    "claim 仅由 transferable 事实支撑，但 claim_type=direct", cid))

        # 7. scope 不得升级
        if facts and level_name:
            claim_level = SCOPE_LEVELS.get(level_name, 0)
            evidence_levels = []
            for fact in facts:
                scope = str(fact.get("scope") or "unspecified")
                if scope in SCOPE_LEVELS:
                    evidence_levels.append(SCOPE_LEVELS[scope])
            if evidence_levels:
                top = max(evidence_levels)
                if claim_level > top:
                    if (top == 0 and claim_level >= 2) or (top == 1 and claim_level >= 2) \
                            or (top == 2 and claim_level >= 3):
                        issues.append(Issue("SCOPE_INFLATION",
                                            f"scope 被升级：「{verb}」高于 evidence 的最高 scope "
                                            f"{reverse_scope(top)}", cid))
                    elif top == 0 and claim_level == 1:
                        issues.append(Issue("SCOPE_OVERREACH",
                                            f"scope 略有升级：「{verb}」高于 evidence scope support", cid))
            elif claim_level >= 2:
                issues.append(Issue("SCOPE_UNVERIFIABLE",
                                    f"evidence 未声明 scope，但 claim 使用「{verb}」，需确认参与层级", cid))

        # 8. 项目事实边界
        issues.extend(check_project_boundary(index, claim, cid, plain, facts))

        # 9. 重复 claim
        for other_text, other_id in seen_texts:
            if similarity(plain, other_text) >= DUPLICATE_SIMILARITY:
                issues.append(Issue("CLAIM_DUPLICATE_TEXT",
                                    f"与 claim {other_id} 高度重复", cid))
                break
        seen_texts.append((plain, cid))

    return issues


def reverse_scope(level: int) -> str:
    for name, value in SCOPE_LEVELS.items():
        if value == level:
            return name
    return "unknown"


def check_project_boundary(index: StoreIndex, claim: Dict[str, Any], cid: str,
                           plain: str, facts: List[Dict[str, Any]]) -> List[Issue]:
    issues: List[Issue] = []
    for fact in facts:
        fid = str(fact.get("id"))
        owner = index.fact_owner.get(fid)
        kind = index.fact_owner_kind.get(fid)
        if not owner or kind != "projects":
            continue
        ptype = str(owner.get("type") or "work_project")
        commercial = owner.get("commercial_delivery")
        delivery = str(owner.get("delivery_status") or "unknown")

        # 商业交付声明：显式 false 直接违规；未声明则要求先确认。
        if commercial is not True:
            term = contains_any(plain, COMMERCIAL_TERMS)
            if term:
                code = "PROJECT_BOUNDARY_VIOLATION" if commercial is False \
                    else "PROJECT_COMMERCIAL_UNVERIFIED"
                detail = "commercial_delivery=false" if commercial is False \
                    else "commercial_delivery 未声明"
                issues.append(Issue(code,
                                    f"{ptype} 被表述为商业交付（触发词：{term}，{detail}）", cid))

        # 只有确属非公司项目时，才禁止表述为公司/在职项目。
        if ptype in NON_WORK_PROJECT_TYPES:
            term = contains_any(plain, COMPANY_SCOPE_TERMS)
            if term:
                issues.append(Issue("PROJECT_TYPE_MISMATCH",
                                    f"{ptype} 被表述为公司项目（触发词：{term}）", cid))

        if delivery == "not_delivered":
            term = contains_any(plain, DELIVERY_TERMS)
            if term:
                issues.append(Issue("PROJECT_DELIVERY_MISMATCH",
                                    f"项目未交付但 claim 声称「{term}」", cid))
    return issues


# --------------------------------------------------------------------------
# 检查：简历文本
# --------------------------------------------------------------------------

def resume_bullets(resume_text: str) -> List[str]:
    out = []
    for raw in str(resume_text or "").splitlines():
        line = raw.strip()
        if line.startswith("- "):
            out.append(strip_markup(line[2:]))
    return out


def check_resume(index: StoreIndex, resume_text: str) -> List[Issue]:
    issues: List[Issue] = []
    if not resume_text:
        return issues

    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, resume_text):
            issues.append(Issue("RESUME_PLACEHOLDER",
                                f"简历中出现占位符 / 内部标记：{pattern}"))

    bullets = resume_bullets(resume_text)
    for i, left in enumerate(bullets):
        if len(left) < 12:
            continue
        for right in bullets[i + 1:]:
            if len(right) < 12:
                continue
            if similarity(left, right) >= DUPLICATE_SIMILARITY:
                issues.append(Issue("RESUME_DUPLICATE_BULLET",
                                    f"重复 bullet：{left[:24]}… ≈ {right[:24]}…"))
                break

    claim_plains = [strip_markup(str(c.get("text") or "")) for c in index.claims]
    norm_resume = normalize(resume_text)

    for claim in index.claims:
        cid = str(claim.get("id") or "?")
        plain = strip_markup(str(claim.get("text") or ""))
        if plain and normalize(plain) not in norm_resume:
            issues.append(Issue("RESUME_CLAIM_TEXT_MISMATCH",
                                f"claim 文本未在简历中找到：{plain[:30]}…", cid))

    for bullet in bullets:
        if not extract_numbers(bullet):
            continue
        if any(similarity(bullet, cp) >= BULLET_MATCH_SIMILARITY for cp in claim_plains):
            continue
        issues.append(Issue("RESUME_BULLET_UNVERIFIED",
                            f"含数字的 bullet 没有对应 claim 记录：{bullet[:30]}…"))

    return issues


def front_section_end(resume_text: str) -> int:
    """前段可见区 = 个人优势 + 最近一段工作经历。

    HR / Hiring Manager 在 10–20 秒内实际扫过的就是这两块。无法解析章节时
    退化为全文比例兜底。
    """
    lines = resume_text.splitlines(keepends=True)
    section = ""
    offset = 0
    personal_end = 0
    work_entry_no = 0
    work_entry_end = 0
    work_section_end = 0
    work_seen = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if work_seen and not work_entry_end:
                work_section_end = offset
            section = stripped[3:].strip()
            work_entry_no = 0
            work_seen = work_seen or section.startswith("工作经历")
        elif stripped.startswith("### ") and section.startswith("工作经历"):
            work_entry_no += 1
            if work_entry_no == 1:
                work_seen = True
            elif work_entry_no == 2 and not work_entry_end:
                work_entry_end = offset
        offset += len(line)
        if section == "个人优势":
            personal_end = offset
        if section.startswith("工作经历"):
            work_section_end = offset
            if work_entry_no == 1:
                work_entry_end = offset

    return work_entry_end or work_section_end or personal_end \
        or int(len(resume_text) * FRONT_SECTION_RATIO)


def check_salience(index: StoreIndex, resume_text: str) -> List[Issue]:
    """核心 evidence 不能全部埋在低优先级位置。

    只检查 claim 的起始位置是否落在前段可见区，避免跨边界误判。
    """
    issues: List[Issue] = []
    if not resume_text:
        return issues
    norm_resume = normalize(resume_text)
    front_length = len(normalize(resume_text[:front_section_end(resume_text)]))
    for claim in index.claims:
        rank = claim.get("relevance_rank")
        if not isinstance(rank, int) or rank > 3:
            continue
        plain = normalize(strip_markup(str(claim.get("text") or "")))
        if not plain:
            continue
        position = norm_resume.find(plain)
        if position < 0 or position < front_length:
            continue
        ratio = int(position / max(1, len(norm_resume)) * 100)
        issues.append(Issue("RECRUITER_SALIENCE_MISSING",
                            f"高相关 claim（rank={rank}）出现在全文 {ratio}% 处，"
                            f"超出前段可见区（个人优势 + 最近一段工作经历）：{plain[:30]}…",
                            str(claim.get("id") or "?")))
    return issues


# --------------------------------------------------------------------------
# 检查：JD 覆盖与 ATS
# --------------------------------------------------------------------------

def fact_supports(index: StoreIndex, term: str) -> bool:
    """只有 confirmed / transferable 事实才算“有证据支持”。

    denied 表示候选人明确否认；unknown 表示没有证据。二者都不能用来支撑
    JD 覆盖度或 ATS 关键词，否则等于用未知冒充已知。
    """
    for fact in index.facts.values():
        if fact.get("status") in ("denied", "unknown"):
            continue
        declared = [str(item) for item in (fact.get("jd_requirements") or [])]
        if any(term == item or term in item or item in term for item in declared if item):
            return True
        if term in str(fact.get("statement") or ""):
            return True
        if term in str(fact.get("entity") or ""):
            return True
    return False


def check_jd(index: StoreIndex, resume_text: str) -> List[Issue]:
    issues: List[Issue] = []
    if not resume_text or not index.jd:
        return issues

    claim_blob = "\n".join(strip_markup(str(c.get("text") or "")) for c in index.claims)
    visible_blob = resume_text + "\n" + claim_blob

    for term in index.requirement_terms():
        if term in visible_blob:
            continue
        if fact_supports(index, term):
            issues.append(Issue("JD_CORE_REQUIREMENT_UNCOVERED",
                                f"JD 核心要求「{term}」有事实支持，但简历与 claim 中均未体现"))
        else:
            issues.append(Issue("JD_REQUIREMENT_NO_EVIDENCE",
                                f"JD 核心要求「{term}」没有事实支持，不得写入简历"))

    for term in index.keyword_terms():
        in_resume = term in resume_text
        supported = fact_supports(index, term)
        if not in_resume and supported:
            issues.append(Issue("ATS_KEYWORD_MISSING",
                                f"ATS 关键词「{term}」有事实支持，但未出现在简历中"))
        elif in_resume and not supported:
            issues.append(Issue("ATS_KEYWORD_UNSUPPORTED",
                                f"简历出现 ATS 关键词「{term}」，但没有任何事实支持，疑似关键词堆砌"))

    return issues


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def validate(store: Dict[str, Any], resume_text: Optional[str] = None) -> List[Issue]:
    issues: List[Issue] = []
    issues.extend(check_structure(store))
    index = StoreIndex(store).build(issues)
    issues.extend(check_claims(index))
    if resume_text:
        issues.extend(check_resume(index, resume_text))
        issues.extend(check_salience(index, resume_text))
        issues.extend(check_jd(index, resume_text))
    return issues


def metric_summary(issues: Sequence[Issue]) -> Dict[str, Dict[str, int]]:
    summary: Dict[str, Dict[str, int]] = {}
    for issue in issues:
        metric = METRIC_BY_CODE.get(issue.code, "unmapped")
        bucket = summary.setdefault(metric, {"error": 0, "warning": 0})
        bucket[issue.severity] = bucket.get(issue.severity, 0) + 1
    return summary


def format_issues(issues: Sequence[Issue]) -> str:
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    lines = []
    for issue in errors:
        lines.append(f"ERROR [{issue.code}] {issue.message}"
                     + (f"  ({issue.location})" if issue.location else ""))
    for issue in warnings:
        lines.append(f"WARN  [{issue.code}] {issue.message}"
                     + (f"  ({issue.location})" if issue.location else ""))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="optimize-resume 事实安全检查（deterministic）")
    parser.add_argument("fact_store", help="fact store YAML 路径")
    parser.add_argument("--resume", help="简历 Markdown 路径，提供后启用简历级与 JD 级检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--fail-on", choices=["error", "warning"], default="error",
                        help="达到该级别即返回非零退出码，默认 error")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    store_path = Path(args.fact_store).expanduser().resolve()
    if not store_path.is_file():
        print(f"错误: fact store 不存在: {store_path}")
        return 2

    try:
        store = load_store(store_path)
    except Exception as exc:  # pragma: no cover
        print(f"错误: 无法解析 fact store: {exc}")
        return 2

    resume_text = None
    if args.resume:
        resume_path = Path(args.resume).expanduser().resolve()
        if not resume_path.is_file():
            print(f"错误: 简历不存在: {resume_path}")
            return 2
        resume_text = resume_path.read_text(encoding="utf-8")

    issues = validate(store, resume_text)
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]

    if args.json:
        print(json.dumps({
            "fact_store": str(store_path),
            "resume": str(args.resume) if args.resume else None,
            "passed": not errors,
            "errors": len(errors),
            "warnings": len(warnings),
            "issues": [i.to_dict() for i in issues],
            "metrics": metric_summary(issues),
        }, ensure_ascii=False, indent=2))
    else:
        text = format_issues(issues)
        if text:
            print(text)
        print(f"事实安全检查：{len(errors)} 个错误，{len(warnings)} 个警告")
        if not errors:
            print("通过：未发现事实安全违规（语义层面仍需 Agent 复核）")

    if errors:
        return 1
    if warnings and args.fail_on == "warning":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
