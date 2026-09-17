#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Eval 公共加载层：路径常量、YAML/Markdown 读取、prompt frontmatter、benchmark 发现。

设计约束：
- 新增一个 benchmark case = 新增一个目录，不需要改代码（自动递归发现 `case.yaml`）；
- case 内引用的文件路径可以相对 case 目录，也可以相对 benchmark 根目录（共享 persona）；
- 缺文件时不静默跳过，而是以明确错误暴露出来，避免把「加载失败」误当成「通过」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# --------------------------------------------------------------------------
# 路径常量
# --------------------------------------------------------------------------

EVALS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = EVALS_DIR.parent

REGRESSION_DIR = EVALS_DIR / "regression"
REGRESSION_CASES_DIR = REGRESSION_DIR / "cases"

QUALITY_DIR = EVALS_DIR / "quality"
BENCHMARK_DIR = QUALITY_DIR / "benchmark"
JUDGES_DIR = QUALITY_DIR / "judges"
RUNS_DIR = QUALITY_DIR / "runs"
REPORTS_DIR = EVALS_DIR / "reports"

BENCHMARK_MANIFEST = BENCHMARK_DIR / "benchmark.yaml"
BENCHMARK_SCHEMA = BENCHMARK_DIR / "case.schema.json"
QUALITY_SCHEMAS_DIR = QUALITY_DIR / "schemas"
PAIRWISE_RESULT_SCHEMA = QUALITY_SCHEMAS_DIR / "pairwise_result.schema.json"
EVIDENCE_RESULT_SCHEMA = QUALITY_SCHEMAS_DIR / "evidence_result.schema.json"
FACT_GUARD_RESULT_SCHEMA = QUALITY_SCHEMAS_DIR / "fact_guard_result.schema.json"


class LoadError(RuntimeError):
    """加载或路径解析失败。必须显式失败，不允许降级成默认值。"""


def require_yaml() -> Any:
    if yaml is None:
        raise LoadError("需要 PyYAML：python3 -m pip install pyyaml")
    return yaml


def load_yaml(path: Path) -> Any:
    require_yaml()
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# prompt 文件（YAML frontmatter + 正文）
# --------------------------------------------------------------------------

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass
class Prompt:
    path: Path
    meta: Dict[str, Any]
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("id") or self.path.stem)

    @property
    def version(self) -> str:
        return str(self.meta.get("version") or "0")

    def fingerprint(self) -> str:
        return f"{self.id}@v{self.version}"


def load_prompt(name: str) -> Prompt:
    """读取 `evals/quality/judges/<name>.md`。

    文件必须以 YAML frontmatter 声明 `id` 与 `version`，否则判为加载错误——
    prompt 版本是报告可复现性的一部分，不能缺省。
    """
    filename = name if name.endswith(".md") else f"{name}.md"
    path = JUDGES_DIR / filename
    if not path.is_file():
        raise LoadError(f"找不到 judge prompt: {path}")
    text = read_text(path)
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise LoadError(f"judge prompt 缺少 YAML frontmatter（需声明 id / version）: {path}")
    meta = load_yaml_text(match.group(1), path)
    if not isinstance(meta, dict) or not meta.get("id") or not meta.get("version"):
        raise LoadError(f"judge prompt frontmatter 必须包含 id 与 version: {path}")
    body = text[match.end():].strip()
    return Prompt(path=path, meta=meta, body=body)


def load_yaml_text(text: str, origin: Optional[Path] = None) -> Any:
    require_yaml()
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:  # pragma: no cover
        raise LoadError(f"YAML 解析失败{f' ({origin})' if origin else ''}: {exc}")


# --------------------------------------------------------------------------
# Golden Benchmark
# --------------------------------------------------------------------------

@dataclass
class BenchmarkCase:
    case_id: str
    dir: Path
    path: Path
    metadata: Dict[str, Any]
    inputs: Dict[str, str]
    expectations: Dict[str, Any]
    human_review: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def role_family(self) -> str:
        return str(self.metadata.get("role_family") or "unknown")

    @property
    def difficulty(self) -> str:
        return str(self.metadata.get("difficulty") or "unknown")

    @property
    def source(self) -> str:
        return str(self.metadata.get("source") or "unknown")

    # -- 输入文件解析 ------------------------------------------------------

    def resolve(self, relative: str) -> Path:
        """case 目录优先，其次 benchmark 根目录（共享 persona）。"""
        candidate = self.dir / relative
        if candidate.is_file():
            return candidate
        shared = BENCHMARK_DIR / relative
        if shared.is_file():
            return shared
        raise LoadError(f"case {self.case_id} 引用的文件不存在: {relative}")

    def path_for(self, key: str) -> Optional[Path]:
        value = self.inputs.get(key)
        if not value:
            return None
        return self.resolve(str(value))

    def source_resume_text(self) -> str:
        path = self.path_for("source_resume")
        return read_text(path) if path else ""

    def fact_store(self) -> Dict[str, Any]:
        path = self.path_for("fact_store")
        if path is None:
            raise LoadError(f"case {self.case_id} 缺少 inputs.fact_store")
        data = load_yaml(path)
        if not isinstance(data, dict):
            raise LoadError(f"case {self.case_id} 的 fact_store 不是 mapping: {path}")
        return data

    def jd_text(self) -> str:
        path = self.path_for("jd")
        if path is None:
            raise LoadError(f"case {self.case_id} 缺少 inputs.jd")
        return read_text(path)

    def context_text(self) -> str:
        path = self.path_for("context")
        return read_text(path) if path else ""

    def jd_core_requirements(self) -> List[str]:
        store = self.fact_store()
        jd = store.get("jd") or {}
        return [str(item) for item in (jd.get("core_requirements") or []) if str(item).strip()]

    def jd_ats_keywords(self) -> List[str]:
        store = self.fact_store()
        jd = store.get("jd") or {}
        return [str(item) for item in (jd.get("ats_keywords") or []) if str(item).strip()]

    # -- 期望 --------------------------------------------------------------

    def must_preserve(self) -> List[str]:
        return [str(item) for item in (self.expectations.get("must_preserve") or [])]

    def forbidden_claims(self) -> List[str]:
        return [str(item) for item in (self.expectations.get("forbidden_claims") or [])]

    def important_capabilities(self) -> List[str]:
        return [str(item) for item in (self.expectations.get("important_capabilities") or [])]

    def capabilities(self) -> List[Dict[str, Any]]:
        """原始 capability 定义（含 weight / surfaces / evidence_facts）。"""
        return [item for item in (self.expectations.get("important_capabilities") or [])
                if isinstance(item, dict)]

    def capability_weight_by_fact(self) -> Dict[str, int]:
        weights: Dict[str, int] = {}
        for cap in self.capabilities():
            weight = int(cap.get("weight") or 2)
            for fact_id in cap.get("evidence_facts") or []:
                fact_id = str(fact_id)
                weights[fact_id] = max(weights.get(fact_id, 0), weight)
        return weights


def discover_benchmark_cases(only: Optional[Sequence[str]] = None,
                             role_family: Optional[str] = None) -> List[BenchmarkCase]:
    if not BENCHMARK_DIR.is_dir():
        raise LoadError(f"benchmark 目录不存在: {BENCHMARK_DIR}")
    case_files = sorted(BENCHMARK_DIR.rglob("case.yaml"))
    only_set = {item for item in (only or []) if item}
    cases: List[BenchmarkCase] = []
    seen: Dict[str, Path] = {}
    for path in case_files:
        data = load_yaml(path)
        if not isinstance(data, dict):
            raise LoadError(f"benchmark case 不是 mapping: {path}")
        case = build_case(path, data)
        if case.case_id in seen:
            raise LoadError(f"case id 重复：{case.case_id}（{seen[case.case_id]} 与 {path}）")
        seen[case.case_id] = path
        if only_set and case.case_id not in only_set:
            continue
        if role_family and case.role_family != role_family:
            continue
        cases.append(case)
    if only_set:
        missing = only_set - set(seen)
        if missing:
            raise LoadError(f"找不到指定 case: {', '.join(sorted(missing))}")
    return cases


def build_case(path: Path, data: Dict[str, Any]) -> BenchmarkCase:
    case_id = str(data.get("id") or "").strip()
    if not case_id:
        raise LoadError(f"benchmark case 缺少 id: {path}")
    inputs = data.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise LoadError(f"case {case_id} 的 inputs 必须是 mapping")
    expectations = data.get("expectations") or {}
    if not isinstance(expectations, dict):
        raise LoadError(f"case {case_id} 的 expectations 必须是 mapping")
    return BenchmarkCase(
        case_id=case_id,
        dir=path.parent,
        path=path,
        metadata=dict(data.get("metadata") or {}),
        inputs={str(k): str(v) for k, v in inputs.items() if v is not None},
        expectations=dict(expectations),
        human_review=dict(data.get("human_review") or {}),
        raw=data,
    )


def benchmark_version() -> Dict[str, Any]:
    if not BENCHMARK_MANIFEST.is_file():
        return {"version": "unknown", "updated_at": "", "description": ""}
    data = load_yaml(BENCHMARK_MANIFEST)
    if not isinstance(data, dict):
        raise LoadError(f"benchmark.yaml 不是 mapping: {BENCHMARK_MANIFEST}")
    return data


def benchmark_stats(cases: Sequence[BenchmarkCase]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for case in cases:
        out[case.role_family] = out.get(case.role_family, 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------
# 版本对比产物（baseline / candidate 输出）
# --------------------------------------------------------------------------

@dataclass
class RunOutput:
    """一个 Skill 版本在某个 case 上的产出。

    `facts` 可以缺失：缺失时 Fact Gate 只能做简历级检查，
    Evidence Recall 会退化为文本匹配，报告里会明确标注 degraded。
    """

    label: str
    resume_path: Optional[Path]
    facts_path: Optional[Path]
    resume_text: str = ""
    facts: Optional[Dict[str, Any]] = None

    @property
    def has_facts(self) -> bool:
        return isinstance(self.facts, dict)

    def to_judge_payload(self) -> Dict[str, Any]:
        """交给 Judge 的内容必须干净：只有简历正文，不带 claims / 版本 / 路径。"""
        return {"resume": self.resume_text}


RUN_SUFFIXES = {
    "baseline": ("baseline.md", "baseline.facts.yaml"),
    "candidate": ("candidate.md", "candidate.facts.yaml"),
}


def load_run_outputs(runs_dir: Path, case_id: str) -> Optional[Tuple[RunOutput, RunOutput]]:
    """读取 `runs_dir/<case_id>/{baseline,candidate}.md[+.facts.yaml]`。

    任一简历缺失时返回 None（表示该 case 没有可比较产物），不伪造内容。
    """
    case_run_dir = Path(runs_dir) / case_id
    if not case_run_dir.is_dir():
        return None

    outputs: Dict[str, RunOutput] = {}
    for label, (resume_name, facts_name) in RUN_SUFFIXES.items():
        resume_path = case_run_dir / resume_name
        if not resume_path.is_file():
            return None
        facts_path = case_run_dir / facts_name
        facts = load_yaml(facts_path) if facts_path.is_file() else None
        outputs[label] = RunOutput(
            label=label,
            resume_path=resume_path,
            facts_path=facts_path if facts_path.is_file() else None,
            resume_text=read_text(resume_path),
            facts=facts if isinstance(facts, dict) else None,
        )
    return outputs["baseline"], outputs["candidate"]


def run_dir_for(case_id: str, runs_dir: Optional[Path] = None) -> Path:
    return Path(runs_dir or RUNS_DIR) / case_id


def write_run_output(case_id: str, label: str, resume_text: str,
                     facts: Optional[Dict[str, Any]] = None,
                     runs_dir: Optional[Path] = None) -> Path:
    require_yaml()
    directory = run_dir_for(case_id, runs_dir)
    directory.mkdir(parents=True, exist_ok=True)
    resume_path = directory / f"{label}.md"
    resume_path.write_text(resume_text, encoding="utf-8")
    if facts is not None:
        facts_path = directory / f"{label}.facts.yaml"
        with facts_path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(facts, handle, allow_unicode=True, sort_keys=False)
    return resume_path
