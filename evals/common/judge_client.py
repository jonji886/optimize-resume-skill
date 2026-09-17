#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Judge 模型接口（provider adapter）。

目标：核心 Eval 逻辑不关心模型供应商，只依赖一个清晰接口：

    class JudgeClient:
        def complete(self, system, user, payload=None) -> str

provider 实现：

    mock        离线确定性启发式，用于 CI / 无 API Key 时验证 Harness
    subprocess  把请求 JSON 打到外部命令的 stdin，取其 stdout（便于接任意自研判定器）
    openai      OpenAI 兼容 HTTP 接口（含各类兼容网关）
    anthropic   Anthropic Messages API

`payload` 是可选的结构化输入：真实模型只用渲染好的 system/user prompt；
mock judge 用 payload 直接计算，避免去解析 prompt 文本。

刻意不做的事：不引入任何第三方 SDK，不做重试编排，不做多模型路由。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .schemas import PAIRWISE_DIMENSIONS, normalize_winner

PROVIDERS = ("mock", "subprocess", "openai", "anthropic")

DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}

DEFAULT_API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    "mock": "mock-heuristic-v1",
    "subprocess": "external",
}


class JudgeError(RuntimeError):
    """Judge 调用失败。必须显式失败，不能降级为「默认 candidate 获胜」。"""


@dataclass
class JudgeConfig:
    provider: str = "mock"
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout: int = 300
    command: str = ""
    endpoint: str = ""
    api_key_env: str = ""
    api_key: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def resolved(self) -> "JudgeConfig":
        cfg = JudgeConfig(**{**self.__dict__})
        cfg.provider = (cfg.provider or "mock").strip().lower()
        if cfg.provider not in PROVIDERS:
            raise JudgeError(f"未知 judge provider: {cfg.provider}（可选 {', '.join(PROVIDERS)}）")
        if not cfg.model:
            cfg.model = DEFAULT_MODELS.get(cfg.provider, "unknown")
        if not cfg.api_key_env:
            cfg.api_key_env = DEFAULT_API_KEY_ENV.get(cfg.provider, "")
        if not cfg.endpoint and cfg.provider in DEFAULT_ENDPOINTS:
            cfg.endpoint = DEFAULT_ENDPOINTS[cfg.provider]
        if cfg.provider == "subprocess" and not cfg.command:
            cfg.command = os.environ.get("EVAL_JUDGE_CMD", "")
        return cfg

    def describe(self) -> Dict[str, Any]:
        """写进报告的 judge 配置指纹（不含密钥）。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "endpoint": self.endpoint or "",
            "api_key_env": self.api_key_env or "",
            "api_key_present": bool(self.resolve_api_key()),
            "command": (self.command.split()[0] if self.command else ""),
        }

    def resolve_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env, "")
        return ""


class JudgeClient:
    name = "base"

    def complete(self, system: str, user: str,
                 payload: Optional[Dict[str, Any]] = None) -> str:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"client": self.name}


# --------------------------------------------------------------------------
# mock：离线确定性启发式
# --------------------------------------------------------------------------

VAGUE_PATTERNS = ("精通", "全面负责", "大幅提升", "显著提升", "能力极强", "经验丰富",
                  "顶尖", "第一", "业界领先")
MIGRATION_MARKERS = ("可迁移", "迁移至", "迁移到", "可复用", "底层能力", "可延展")


class MockJudgeClient(JudgeClient):
    """确定性启发式 judge，**不是真实质量判断**。

    存在的唯一目的：在没有 API Key 的环境（CI、本地验证）下把整条
    Harness（benchmark 加载 → fact gate → A/B 随机化 → 结构化解析 → 聚合 →
    报告）跑通，并保证结果可复现。

    报告里必须标注 `judge.provider == mock`，任何版本结论都不能基于它。
    """

    name = "mock"

    def complete(self, system: str, user: str,
                 payload: Optional[Dict[str, Any]] = None) -> str:
        if not payload:
            raise JudgeError("mock judge 需要结构化 payload")
        candidates = payload.get("candidates") or {}
        stats = {side: self._stats(str(candidates.get(side) or "")) for side in ("A", "B")}
        # 把 rubric 里声明的维度作为权重来源，保持与真实 judge 的维度一致。
        dims = list(payload.get("dimensions") or PAIRWISE_DIMENSIONS)
        dim_winners: Dict[str, Dict[str, str]] = {}
        scores: Dict[str, float] = {"A": 0.0, "B": 0.0}
        for dim in dims:
            a = stats["A"].get(dim, 0.0)
            b = stats["B"].get(dim, 0.0)
            scores["A"] += a
            scores["B"] += b
            if abs(a - b) < 0.02:
                winner, reason = "Tie", f"差异小于 0.02（A={a:.2f} / B={b:.2f}）"
            else:
                winner = "A" if a > b else "B"
                reason = f"{winner} 更高（A={a:.2f} / B={b:.2f}）"
            dim_winners[dim] = {"winner": winner, "reason": reason}

        count = max(1, len(dims))
        a_score, b_score = scores["A"] / count, scores["B"] / count
        diff = a_score - b_score
        if abs(diff) < 0.02:
            overall_winner = "Tie"
        else:
            overall_winner = "A" if diff > 0 else "B"
        confidence = min(0.9, 0.5 + abs(diff))

        result: Dict[str, Any] = dict(dim_winners)
        result["overall"] = {
            "winner": overall_winner,
            "confidence": round(confidence, 3),
            "reason": f"mock 启发式：A={a_score:.3f} / B={b_score:.3f}（非真实质量判断）",
        }
        return json.dumps(result, ensure_ascii=False)

    # -- 启发式特征 --------------------------------------------------------

    def _stats(self, text: str) -> Dict[str, float]:
        text = text or ""
        bullets = [line.strip()[2:].strip()
                   for line in text.splitlines() if line.strip().startswith("- ")]
        blob = text
        capabilities = [str(item) for item in (self._capabilities or [])]
        ats = [str(item) for item in (self._ats or [])]

        coverage = self._coverage(blob, capabilities) if capabilities else 0.5

        front = 0.5
        if capabilities and blob:
            positions = [blob.find(cap) for cap in capabilities]
            hits = [p for p in positions if p >= 0]
            if hits:
                ratio = min(hits) / max(1, len(blob))
                front = max(0.0, 1.0 - ratio * 3.0)

        # 信息密度代理：去重后的有效内容占比 ×（1 - 超长 bullet 比例）。
        # 「更长」不应该等价于「更好」，所以这里惩罚的是填篇幅与重复，而不是绝对长度。
        density = 0.5
        if bullets:
            normalized_bullets = [re.sub(r"\s+", "", b) for b in bullets]
            total_chars = sum(len(b) for b in normalized_bullets) or 1
            unique_chars = sum(len(b) for b in set(normalized_bullets))
            dedup_ratio = unique_chars / total_chars
            long_ratio = sum(1 for b in normalized_bullets if len(b) > 80) / len(normalized_bullets)
            density = max(0.0, dedup_ratio * (1.0 - long_ratio))

        redundancy = 0.0
        if len(bullets) > 1:
            normalized = [re.sub(r"\s+", "", b) for b in bullets]
            unique = len(set(normalized))
            redundancy = unique / len(normalized)

        ats_score = self._ats_score(blob, ats)
        specificity = 0.0
        if bullets:
            with_numbers = sum(1 for b in bullets if re.search(r"\d", b))
            specificity = with_numbers / len(bullets)
        defensibility = 1.0
        if bullets:
            vague = sum(1 for b in bullets if any(p in b for p in VAGUE_PATTERNS))
            hedged = sum(1 for b in bullets if any(p in b for p in MIGRATION_MARKERS))
            defensibility = max(0.0, 1.0 - vague / len(bullets) + 0.1 * hedged / len(bullets))

        return {
            "jd_evidence_coverage": coverage,
            "evidence_strength": 0.5 * specificity + 0.5 * coverage,
            "recruiter_salience": front,
            "information_density": density,
            "redundancy_conciseness": redundancy,
            "ats_terminology": ats_score,
            "interview_defensibility": min(1.0, defensibility),
        }

    _capabilities: Sequence[str] = ()
    _ats: Sequence[str] = ()

    @staticmethod
    def _coverage(blob: str, terms: Sequence[str]) -> float:
        if not terms:
            return 0.5
        hit = sum(1 for term in terms if _term_in(blob, term))
        return hit / len(terms)

    @staticmethod
    def _ats_score(blob: str, terms: Sequence[str]) -> float:
        """关键词覆盖，同时对堆砌扣分：同一关键词出现次数 > 3 视为堆砌。"""
        if not terms:
            return 0.5
        score = 0.0
        for term in terms:
            count = blob.count(term)
            if count == 0:
                continue
            score += 1.0 if count <= 3 else max(0.2, 1.0 - (count - 3) * 0.2)
        return score / len(terms)


def _term_in(blob: str, term: str) -> bool:
    """中英混合的能力词匹配：英文词按小写子串，中文按原样子串。"""
    if not term:
        return False
    if re.search(r"[A-Za-z]", term):
        return term.lower() in blob.lower()
    return term in blob


# --------------------------------------------------------------------------
# subprocess
# --------------------------------------------------------------------------

class SubprocessJudgeClient(JudgeClient):
    name = "subprocess"

    def __init__(self, command: str, timeout: int = 300) -> None:
        if not command.strip():
            raise JudgeError("subprocess judge 未配置命令（--judge-cmd 或 EVAL_JUDGE_CMD）")
        self.command = command
        self.timeout = timeout

    def complete(self, system: str, user: str,
                 payload: Optional[Dict[str, Any]] = None) -> str:
        request = json.dumps({
            "system": system,
            "user": user,
            "payload": payload or {},
        }, ensure_ascii=False)
        try:
            proc = subprocess.run(shlex.split(self.command), input=request,
                                  capture_output=True, text=True, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            raise JudgeError(f"subprocess judge 调用失败: {exc}") from exc
        if proc.returncode != 0:
            raise JudgeError(f"subprocess judge 退出码 {proc.returncode}: {proc.stderr.strip()[:200]}")
        return proc.stdout

    def describe(self) -> Dict[str, Any]:
        return {"client": self.name, "command": self.command.split()[0] if self.command else ""}


# --------------------------------------------------------------------------
# HTTP providers
# --------------------------------------------------------------------------

class HttpJudgeClient(JudgeClient):
    def __init__(self, config: JudgeConfig) -> None:
        self.config = config
        key = config.resolve_api_key()
        if not key:
            env = config.api_key_env or "(未设置)"
            raise JudgeError(
                f"{config.provider} judge 缺少 API Key：请设置环境变量 {env}，"
                f"或改用 --mock / --dry-run 离线验证 Harness")
        self.api_key = key

    def _post(self, url: str, body: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - 网络路径
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise JudgeError(f"judge HTTP {exc.code}: {detail}") from exc
        except Exception as exc:  # pragma: no cover - 网络路径
            raise JudgeError(f"judge 请求失败: {exc}") from exc


class OpenAICompatibleJudgeClient(HttpJudgeClient):
    name = "openai"

    def complete(self, system: str, user: str,
                 payload: Optional[Dict[str, Any]] = None) -> str:
        url = self.config.endpoint.rstrip("/") + "/chat/completions"
        body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        response = self._post(url, body, {"Authorization": f"Bearer {self.api_key}"})
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise JudgeError(f"judge 响应结构异常: {str(response)[:200]}") from exc


class AnthropicJudgeClient(HttpJudgeClient):
    name = "anthropic"

    def complete(self, system: str, user: str,
                 payload: Optional[Dict[str, Any]] = None) -> str:
        url = self.config.endpoint.rstrip("/") + "/v1/messages"
        body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        response = self._post(url, body, {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        })
        try:
            parts = response["content"]
            return "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        except (KeyError, TypeError) as exc:
            raise JudgeError(f"judge 响应结构异常: {str(response)[:200]}") from exc


# --------------------------------------------------------------------------
# 工厂
# --------------------------------------------------------------------------

def build_judge_client(config: JudgeConfig) -> JudgeClient:
    cfg = config.resolved()
    if cfg.provider == "mock":
        return MockJudgeClient()
    if cfg.provider == "subprocess":
        return SubprocessJudgeClient(cfg.command, timeout=cfg.timeout)
    if cfg.provider == "openai":
        return OpenAICompatibleJudgeClient(cfg)
    if cfg.provider == "anthropic":
        return AnthropicJudgeClient(cfg)
    raise JudgeError(f"未知 judge provider: {cfg.provider}")


def judge_config_from_env(overrides: Optional[Dict[str, Any]] = None) -> JudgeConfig:
    cfg = JudgeConfig(
        provider=os.environ.get("QUALITY_JUDGE_PROVIDER", ""),
        model=os.environ.get("QUALITY_JUDGE_MODEL", ""),
        command=os.environ.get("QUALITY_JUDGE_CMD", "") or os.environ.get("EVAL_JUDGE_CMD", ""),
        endpoint=os.environ.get("QUALITY_JUDGE_ENDPOINT", ""),
        api_key_env=os.environ.get("QUALITY_JUDGE_API_KEY_ENV", ""),
    )
    temperature = os.environ.get("QUALITY_JUDGE_TEMPERATURE", "")
    if temperature:
        try:
            cfg.temperature = float(temperature)
        except ValueError as exc:
            raise JudgeError(f"QUALITY_JUDGE_TEMPERATURE 不是数字: {temperature}") from exc
    if not cfg.provider:
        cfg.provider = "mock"
    for key, value in (overrides or {}).items():
        if value is not None:
            setattr(cfg, key, value)
    return cfg.resolved()


def attach_mock_terms(client: JudgeClient, capabilities: Sequence[str],
                      ats_keywords: Sequence[str]) -> None:
    """把 case 的评测词表交给 mock judge（真实 judge 走 prompt，不需要这个）。"""
    if isinstance(client, MockJudgeClient):
        client._capabilities = list(capabilities)
        client._ats = list(ats_keywords)


__all__ = [
    "JudgeConfig",
    "JudgeClient",
    "JudgeError",
    "MockJudgeClient",
    "SubprocessJudgeClient",
    "OpenAICompatibleJudgeClient",
    "AnthropicJudgeClient",
    "build_judge_client",
    "judge_config_from_env",
    "attach_mock_terms",
    "PROVIDERS",
]
