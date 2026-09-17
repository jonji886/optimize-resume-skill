#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Eval Harness 自身的测试。

评测系统比被测系统更需要测试：一个默默判错的 Harness 比没有 Harness 更危险。
这些测试全部离线运行（无网络、无 API Key），覆盖：

    - A/B 位置交换后的结果映射与 POSITION_INCONSISTENT 判定
    - Fact Gate 的否决权（事实错误不能被表达质量抵消）
    - judge 输出解析与结构校验失败的处理
    - 聚合指标的分母口径（Position Inconsistent / Both Fact Fail 的剔除）
    - Golden Benchmark 结构校验
    - mock 产物对全部 benchmark case 都通过 Fact Gate
    - 报告落盘

运行：

    python3 -m unittest discover -s evals/tests -t . -v
    python3 evals/tests/test_eval_harness.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evals.common import loaders as ld  # noqa: E402
from evals.common import report as rp  # noqa: E402
from evals.common import schemas as sc  # noqa: E402
from evals.common.judge_client import JudgeClient, JudgeError, MockJudgeClient  # noqa: E402
from evals.quality import evidence as ev  # noqa: E402
from evals.quality import fact_gate as fg  # noqa: E402
from evals.quality import mock_generator as mg  # noqa: E402
from evals.quality import pairwise as pw  # noqa: E402

SYSTEM_PROMPT = "rubric placeholder"


# --------------------------------------------------------------------------
# Judge stubs
# --------------------------------------------------------------------------

class FixedWinnerJudge(JudgeClient):
    """永远返回固定 winner 的 judge，用来暴露位置偏差。"""

    name = "fixed"

    def __init__(self, winner: str) -> None:
        self.winner = winner
        self.calls = 0

    def complete(self, system, user, payload=None):
        self.calls += 1
        return _result_json(self.winner)


class LengthBiasJudge(JudgeClient):
    """偏爱更长文本的 judge：用于验证「内容驱动」的判定能被正确映射。"""

    name = "length"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system, user, payload=None):
        self.calls += 1
        candidates = (payload or {}).get("candidates") or {}
        a, b = str(candidates.get("A") or ""), str(candidates.get("B") or "")
        return _result_json("A" if len(a) >= len(b) else "B")


class MalformedJudge(JudgeClient):
    name = "malformed"

    def complete(self, system, user, payload=None):
        return "sorry, I cannot produce JSON today"


class ExplodingJudge(JudgeClient):
    name = "exploding"

    def complete(self, system, user, payload=None):
        raise JudgeError("boom")


class IncompleteJudge(JudgeClient):
    name = "incomplete"

    def complete(self, system, user, payload=None):
        return '{"overall": {"winner": "B", "confidence": 0.7, "reason": "x"}}'


def _result_json(winner: str, confidence: float = 0.8) -> str:
    dims = {}
    for dim in sc.PAIRWISE_DIMENSIONS:
        dims[dim] = {"winner": winner, "reason": "stub"}
    dims["overall"] = {"winner": winner, "confidence": confidence, "reason": "stub overall"}
    import json
    return json.dumps(dims, ensure_ascii=False)


# --------------------------------------------------------------------------
# 测试用最小 fact store
# --------------------------------------------------------------------------

def ground_truth_store() -> dict:
    return {
        "meta": {"candidate": "测试候选人", "schema_version": 1},
        "jd": {"title": "AI 解决方案工程师",
               "core_requirements": ["API 集成"],
               "ats_keywords": ["OpenAPI"]},
        "experiences": [{
            "id": "corp-a",
            "employer": "某科技有限公司",
            "role": "技术服务工程师",
            "type": "work",
            "facts": [
                {"id": "fact-001",
                 "statement": "负责企业客户 OpenAPI 接入与上线，累计支撑 80+ 应用上线",
                 "status": "confirmed", "scope": "core_execution",
                 "source": "original_resume", "numbers": ["80"],
                 "jd_requirements": ["API 集成"]},
                {"id": "fact-002",
                 "statement": "直接参与客户需求调研",
                 "status": "denied", "scope": "support",
                 "source": "user_correction",
                 "match_terms": ["客户需求调研", "需求调研"]},
            ],
        }],
        "claims": [],
    }


def honest_resume() -> tuple:
    store = ground_truth_store()
    store["claims"] = [{
        "id": "claim-001",
        "text": "负责企业客户 OpenAPI 接入与上线，累计支撑 80+ 应用上线",
        "evidence": ["fact-001"],
    }]
    resume = "\n".join([
        "# 测试候选人",
        "手机/微信：13800000000 ｜邮箱：a@example.com",
        "## 个人优势",
        "- **API 集成**：负责企业客户 OpenAPI 接入与上线，累计支撑 80+ 应用上线。",
        "## 工作经历",
        "### 某科技有限公司｜技术服务工程师",
        "2021-07 ~ 2025-06",
        "- **开放平台接入**：负责企业客户 OpenAPI 接入与上线，累计支撑 80+ 应用上线。",
    ])
    return store, resume


def fabricated_resume() -> tuple:
    store, resume = honest_resume()
    store = copy.deepcopy(store)
    store["claims"][0]["text"] = "负责企业客户 OpenAPI 接入与上线，提升接入效率 40%"
    resume = resume.replace("累计支撑 80+ 应用上线", "提升接入效率 40%")
    return store, resume


def denied_reintroduced_resume() -> tuple:
    store, resume = honest_resume()
    return store, resume + "\n- **需求调研**：直接参与客户需求调研。\n"


# --------------------------------------------------------------------------
# Position swap
# --------------------------------------------------------------------------

class PositionSwapTests(unittest.TestCase):
    def _run(self, judge, texts=None):
        texts = texts or {"baseline": "短文本", "candidate": "长文本" * 10}
        return pw.run_pairwise(
            case_id="t-001", texts=texts, client=judge, system_prompt=SYSTEM_PROMPT,
            jd_text="JD", ground_truth=ground_truth_store(),
            source_resume="SR", capabilities=[], ats_keywords=[], seed=0)

    def test_positions_are_mirrored(self):
        judge = FixedWinnerJudge("A")
        outcome = self._run(judge)
        self.assertEqual(judge.calls, 2, "每个 case 必须跑两轮")
        first, second = outcome.rounds
        self.assertEqual(first.order["A"], "baseline")
        self.assertEqual(first.order["B"], "candidate")
        self.assertEqual(second.order["A"], "candidate")
        self.assertEqual(second.order["B"], "baseline")

    def test_position_preference_is_flagged_inconsistent(self):
        outcome = self._run(FixedWinnerJudge("A"))
        self.assertFalse(outcome.position_consistent)
        self.assertIsNone(outcome.winner)

    def test_position_preference_b_side_also_inconsistent(self):
        outcome = self._run(FixedWinnerJudge("B"))
        self.assertFalse(outcome.position_consistent)
        self.assertIsNone(outcome.winner)

    def test_content_driven_winner_maps_to_real_version(self):
        # 长文本 = candidate，两轮都应由内容决定 → 一致判给 candidate
        outcome = self._run(LengthBiasJudge())
        self.assertTrue(outcome.position_consistent)
        self.assertEqual(outcome.winner, pw.LABEL_CANDIDATE)

    def test_tie_both_rounds_is_consistent_tie(self):
        outcome = self._run(FixedWinnerJudge("Tie"))
        self.assertTrue(outcome.position_consistent)
        self.assertEqual(outcome.winner, pw.LABEL_TIE)

    def test_win_then_tie_is_inconsistent(self):
        class WinThenTie(JudgeClient):
            name = "winthen"

            def __init__(self):
                self.calls = 0

            def complete(self, system, user, payload=None):
                self.calls += 1
                return _result_json("A" if self.calls == 1 else "Tie")

        outcome = self._run(WinThenTie())
        self.assertFalse(outcome.position_consistent,
                         "一轮分出胜负、一轮平局不足以支持版本结论")

    def test_malformed_output_is_judge_error(self):
        outcome = self._run(MalformedJudge())
        self.assertIsNotNone(outcome.error)
        self.assertIsNone(outcome.winner)

    def test_incomplete_output_is_judge_error(self):
        outcome = self._run(IncompleteJudge())
        self.assertIsNotNone(outcome.error)

    def test_judge_exception_is_judge_error(self):
        outcome = self._run(ExplodingJudge())
        self.assertIsNotNone(outcome.error)
        self.assertIn("boom", outcome.error)

    def test_seed_changes_first_round_order(self):
        orders = {pw.build_orders(seed)[0] for seed in range(20)}
        self.assertEqual(len(orders), 2, "seed 应该能决定第一轮谁在 A 位")
        for seed in range(20):
            first, second = pw.build_orders(seed)
            self.assertEqual((first[1], first[0]), second, "第二轮必须是第一轮的镜像")


# --------------------------------------------------------------------------
# Fact Gate
# --------------------------------------------------------------------------

class FactGateTests(unittest.TestCase):
    def test_honest_resume_passes(self):
        store, resume = honest_resume()
        result = fg.evaluate(store, resume, ground_truth_store())
        self.assertTrue(result.passed, [v.code for v in result.violations])

    def test_fabricated_metric_fails(self):
        store, resume = fabricated_resume()
        result = fg.evaluate(store, resume, ground_truth_store())
        self.assertFalse(result.passed)
        self.assertIn("fabricated_metric", result.categories())

    def test_denied_fact_reintroduced_fails(self):
        store, resume = denied_reintroduced_resume()
        result = fg.evaluate(store, resume, ground_truth_store())
        self.assertFalse(result.passed)
        self.assertIn("denied_fact", result.categories())

    def test_missing_facts_sidecar_degrades_but_still_checks_denied(self):
        _, resume = denied_reintroduced_resume()
        result = fg.evaluate(None, resume, ground_truth_store())
        self.assertFalse(result.passed)
        self.assertEqual(result.scope, "resume_only")
        self.assertTrue(any("退化" in note for note in result.notes))

    def test_semantic_result_cannot_override_deterministic_failure(self):
        store, resume = fabricated_resume()
        result = fg.evaluate(store, resume, ground_truth_store())
        self.assertFalse(result.passed)
        result = fg.apply_semantic_result(result, {"pass": True}, model="stub")
        self.assertFalse(result.passed, "语义层只能增加否决项，不能把失败改判为通过")

    def test_numeric_bullet_without_claim_is_gated(self):
        """回归：数字 bullet 未登记 claim 时，validate_claims 只报 warning。

        Fact Gate 必须把它升级为否决项，否则「往简历里塞一个没登记的数字」
        可以绕过硬门禁——这是虚构指标最直接的形态。
        """
        store, resume = honest_resume()
        resume += "\n- **效率提升**：将客户接入效率提升 40%。\n"
        result = fg.evaluate(store, resume, ground_truth_store())
        self.assertFalse(result.passed)
        self.assertIn("RESUME_BULLET_UNVERIFIED",
                      [v.code for v in result.violations])
        self.assertIn("fabricated_metric", result.categories())

    def test_scope_warning_only_gates_under_strict_mode(self):
        store, resume = honest_resume()
        # evidence scope 未声明 + claim 使用主导级表达 → SCOPE_UNVERIFIABLE（warning）
        loose = copy.deepcopy(store)
        loose["experiences"][0]["facts"][0]["scope"] = "unspecified"
        loose["claims"][0]["text"] = "主导企业客户 OpenAPI 接入与上线"
        resume = resume.replace("负责企业客户 OpenAPI 接入与上线", "主导企业客户 OpenAPI 接入与上线")
        self.assertTrue(fg.evaluate(loose, resume, ground_truth_store()).passed)
        strict = fg.evaluate(loose, resume, ground_truth_store(), strict=True)
        self.assertFalse(strict.passed)
        self.assertIn("scope_inflation", strict.categories())

    def test_forbidden_claims_are_diagnostic_not_gate(self):
        store, resume = honest_resume()
        resume += "\n- **客户成功**：主导客户需求调研与续约经营。\n"
        result = fg.evaluate(store, resume, ground_truth_store(),
                             forbidden_claims=["主导客户需求调研"])
        self.assertTrue(result.forbidden_hits, "禁写项应被识别为诊断信号")
        self.assertNotIn("forbidden_claim", result.categories())


# --------------------------------------------------------------------------
# Evidence Recall
# --------------------------------------------------------------------------

class EvidenceRecallTests(unittest.TestCase):
    CAPABILITIES = [{
        "capability": "api_integration",
        "label": "开放平台集成",
        "surfaces": ["OpenAPI"],
        "evidence_facts": ["fact-001"],
        "weight": 3,
    }]

    def test_missing_evidence_is_excluded_from_denominator(self):
        store, resume = honest_resume()
        result = ev.compute_evidence_recall(
            ground_truth_store(), resume, self.CAPABILITIES,
            must_preserve=["fact-002", "fact-999"], run_store=store)
        self.assertEqual(result.available_weight, 3, "denied fact 不计入分母")
        self.assertEqual(result.recall, 1.0)
        self.assertEqual(result.must_preserve_missed, [],
                         "must_preserve 中不可用的 fact 不应被记为 miss")

    def test_missing_high_value_evidence_lowers_recall(self):
        store, resume = honest_resume()
        trimmed = resume.replace("累计支撑 80+ 应用上线", "完成接入")
        result = ev.compute_evidence_recall(
            ground_truth_store(), trimmed, self.CAPABILITIES,
            must_preserve=["fact-001"], run_store=store)
        self.assertEqual(result.recall, 0.0)
        self.assertEqual(result.must_preserve_missed, ["fact-001"])

    def test_without_claims_sidecar_falls_back_to_text_and_flags_degraded(self):
        _, resume = honest_resume()
        result = ev.compute_evidence_recall(
            ground_truth_store(), resume, self.CAPABILITIES, run_store=None)
        self.assertFalse(result.based_on_claims)
        self.assertEqual(result.recall, 1.0)
        self.assertTrue(any("degraded" in note for note in result.notes))


# --------------------------------------------------------------------------
# 聚合口径
# --------------------------------------------------------------------------

class AggregationTests(unittest.TestCase):
    def _result(self, outcome, **extra):
        payload = {"case_id": f"c-{outcome}", "role_family": "ai-fde",
                   "difficulty": "medium", "source": "synthetic",
                   "outcome": outcome, "decided_by": "judge", "fact_gate": {}}
        payload.update(extra)
        return payload

    def test_denominator_excludes_inconsistent_and_fact_failures(self):
        results = [
            self._result(rp.OUTCOME_CANDIDATE_WIN),
            self._result(rp.OUTCOME_CANDIDATE_WIN),
            self._result(rp.OUTCOME_BASELINE_WIN),
            self._result(rp.OUTCOME_TIE),
            self._result(rp.OUTCOME_POSITION_INCONSISTENT),
            self._result(rp.OUTCOME_BOTH_FACT_FAIL),
            self._result(rp.OUTCOME_CANDIDATE_FACT_FAIL),
            self._result(rp.OUTCOME_JUDGE_ERROR),
        ]
        agg = rp.aggregate(results)
        metrics = agg["metrics"]
        self.assertEqual(metrics["total_cases"], 8, "原始总数必须保留")
        self.assertEqual(metrics["valid_comparable_cases"], 4)
        self.assertAlmostEqual(metrics["candidate_win_rate"], 0.5)
        self.assertAlmostEqual(metrics["candidate_loss_rate"], 0.25)
        self.assertAlmostEqual(metrics["tie_rate"], 0.25)
        self.assertAlmostEqual(metrics["position_inconsistency_rate"], 0.2)
        self.assertEqual(agg["regression_cases"], ["c-BASELINE_WIN"])
        self.assertEqual(agg["inconsistent_cases"], ["c-POSITION_INCONSISTENT"])

    def test_none_rates_when_nothing_comparable(self):
        agg = rp.aggregate([self._result(rp.OUTCOME_BOTH_FACT_FAIL)])
        self.assertIsNone(agg["metrics"]["candidate_win_rate"])

    def test_human_agreement_is_computed_when_labels_present(self):
        results = [
            self._result(rp.OUTCOME_CANDIDATE_WIN,
                         human_review={"winner": "B", "reviewer": "h"}),
            self._result(rp.OUTCOME_BASELINE_WIN,
                         human_review={"winner": "B", "reviewer": "h"}),
            self._result(rp.OUTCOME_TIE, human_review={}),
        ]
        human = rp.aggregate(results)["human_agreement"]
        self.assertEqual(human["labelled_cases"], 2)
        self.assertEqual(human["agreements"], 1)
        self.assertAlmostEqual(human["agreement_rate"], 0.5)

    def test_dimension_tally_uses_consistent_cases_only(self):
        """回归：维度倾向必须读 `dimensions`（已是真实版本标签），且剔除位置不一致的 case。"""
        consistent = self._result(
            rp.OUTCOME_CANDIDATE_WIN, position_consistency=True,
            rounds=[{"index": 1}],
            dimensions={"jd_evidence_coverage": "candidate",
                        "recruiter_salience": "baseline",
                        "information_density": "tie"})
        inconsistent = self._result(
            rp.OUTCOME_POSITION_INCONSISTENT, position_consistency=False,
            rounds=[{"index": 1}],
            dimensions={"jd_evidence_coverage": "baseline"})
        dims = rp.aggregate([consistent, inconsistent])["dimension_wins"]
        self.assertEqual(dims["jd_evidence_coverage"]["candidate"], 1)
        self.assertEqual(dims["jd_evidence_coverage"]["baseline"], 0,
                         "位置不一致的 case 不应计入维度倾向")
        self.assertEqual(dims["recruiter_salience"]["baseline"], 1)
        self.assertEqual(dims["information_density"]["tie"], 1)

    def test_markdown_report_renders(self):
        results = [self._result(rp.OUTCOME_CANDIDATE_WIN,
                                dimensions={"jd_evidence_coverage": "candidate"},
                                overall={"winner": "candidate", "confidence": 0.7,
                                         "reason": "r"},
                                evidence_recall={})]
        agg = rp.aggregate(results)
        meta = rp.build_meta({"baseline_version": "v1", "candidate_version": "v2",
                              "mode": "real"})
        text = rp.render_markdown(meta, agg, results)
        self.assertIn("Candidate Win Rate", text)
        self.assertIn("Fact Safety", text)


# --------------------------------------------------------------------------
# Judge 输出解析
# --------------------------------------------------------------------------

class JudgeParsingTests(unittest.TestCase):
    def test_normalize_winner_variants(self):
        self.assertEqual(sc.normalize_winner("a"), "A")
        self.assertEqual(sc.normalize_winner(" B "), "B")
        self.assertEqual(sc.normalize_winner("tie"), "Tie")
        self.assertEqual(sc.normalize_winner("平局"), "Tie")
        self.assertIsNone(sc.normalize_winner("A|B|Tie"))
        self.assertIsNone(sc.normalize_winner(None))
        self.assertIsNone(sc.normalize_winner(True))

    def test_extract_json_from_fenced_output(self):
        text = "here you go:\n```json\n{\"overall\": {\"winner\": \"A\"}}\n```\nthanks"
        self.assertEqual(sc.extract_json_object(text), {"overall": {"winner": "A"}})

    def test_extract_json_returns_none_for_prose(self):
        self.assertIsNone(sc.extract_json_object("no json here"))

    def test_pairwise_validation_reports_missing_dimensions(self):
        problems = sc.validate_pairwise_result({"overall": {"winner": "A",
                                                           "confidence": 0.5,
                                                           "reason": "x"}})
        self.assertTrue(any("jd_evidence_coverage" in p for p in problems))

    def test_pairwise_validation_rejects_combined_winner(self):
        data = {dim: {"winner": "A", "reason": "x"} for dim in sc.PAIRWISE_DIMENSIONS}
        data["overall"] = {"winner": "A", "confidence": 1.4, "reason": "x"}
        problems = sc.validate_pairwise_result(data)
        self.assertTrue(any("confidence" in p for p in problems))


# --------------------------------------------------------------------------
# Golden Benchmark
# --------------------------------------------------------------------------

class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = ld.discover_benchmark_cases()

    def test_benchmark_has_expected_size(self):
        self.assertGreaterEqual(len(self.cases), 10)

    def test_all_cases_validate(self):
        problems = []
        for case in self.cases:
            problems.extend(sc.validate_benchmark_case(case))
        self.assertEqual(problems, [])

    def test_cases_match_json_schema(self):
        if sc.json_schema_status() != "available":
            self.skipTest("jsonschema 未安装")
        problems = []
        for case in self.cases:
            problems.extend(sc.validate_json_schema(
                case.raw, ld.BENCHMARK_SCHEMA, case.case_id))
        self.assertEqual(problems, [])

    def test_quality_schema_files_are_valid_json(self):
        import json
        for path in (ld.BENCHMARK_SCHEMA, ld.PAIRWISE_RESULT_SCHEMA,
                     ld.EVIDENCE_RESULT_SCHEMA, ld.FACT_GUARD_RESULT_SCHEMA):
            with path.open("r", encoding="utf-8") as handle:
                schema = json.load(handle)
            self.assertIn("$schema", schema, path.name)

    def test_role_family_coverage(self):
        families = {case.role_family for case in self.cases}
        for expected in ("ai-fde", "ai-solutions", "technical-support"):
            self.assertIn(expected, families)

    def test_real_world_ratio_is_tracked(self):
        synthetic = sum(1 for case in self.cases if case.source == "synthetic")
        self.assertLess(synthetic / len(self.cases), 0.5,
                        "benchmark 应以真实案例为主，synthetic 只用于边界")

    def test_must_preserve_references_existing_facts(self):
        # validate_benchmark_case 已检查，这里再确认检查器真的在跑
        case = self.cases[0]
        original = list(case.expectations["must_preserve"])
        case.expectations["must_preserve"] = ["fact-does-not-exist"]
        problems = sc.validate_benchmark_case(case)
        case.expectations["must_preserve"] = original
        self.assertTrue(any("不存在的 fact" in p for p in problems))


# --------------------------------------------------------------------------
# mock 产物必须全部通过 Fact Gate
# --------------------------------------------------------------------------

class MockGeneratorTests(unittest.TestCase):
    def test_mock_outputs_pass_fact_gate_for_every_case(self):
        failures = []
        for case in ld.discover_benchmark_cases():
            store = case.fact_store()
            for policy in (mg.POLICY_STRONG, mg.POLICY_WEAK):
                output = mg.generate(case.case_id, store, case.capabilities(), policy)
                result = fg.evaluate(output.facts, output.resume_text, store)
                if not result.passed:
                    failures.append(
                        f"{case.case_id}/{policy}: "
                        + ", ".join(v.code for v in result.violations))
        self.assertEqual(failures, [], "mock fixture 自身必须守住事实边界")

    def test_mock_modes_produce_expected_direction(self):
        case = ld.discover_benchmark_cases(only=["ai-fde-001"])[0]
        store = case.fact_store()

        baseline, candidate = mg.generate_pair(
            case.case_id, store, case.capabilities(), "candidate-wins")
        self.assertEqual(baseline.policy, mg.POLICY_WEAK)
        self.assertEqual(candidate.policy, mg.POLICY_STRONG)

        baseline, candidate = mg.generate_pair(
            case.case_id, store, case.capabilities(), "baseline-wins")
        self.assertEqual(baseline.policy, mg.POLICY_STRONG)
        self.assertEqual(candidate.policy, mg.POLICY_WEAK)

        baseline, candidate = mg.generate_pair(
            case.case_id, store, case.capabilities(), "equal")
        self.assertEqual(baseline.resume_text, candidate.resume_text)

    def test_strong_policy_covers_more_evidence_than_weak(self):
        case = ld.discover_benchmark_cases(only=["ai-fde-001"])[0]
        store = case.fact_store()
        strong = mg.generate(case.case_id, store, case.capabilities(),
                             mg.POLICY_STRONG)
        weak = mg.generate(case.case_id, store, case.capabilities(), mg.POLICY_WEAK)
        strong_recall = ev.compute_evidence_recall(
            store, strong.resume_text, case.capabilities(),
            must_preserve=case.must_preserve(), run_store=strong.facts)
        weak_recall = ev.compute_evidence_recall(
            store, weak.resume_text, case.capabilities(),
            must_preserve=case.must_preserve(), run_store=weak.facts)
        self.assertGreater(strong_recall.recall, weak_recall.recall)

    def test_denied_and_unknown_facts_never_enter_mock_output(self):
        for case in ld.discover_benchmark_cases():
            store = case.fact_store()
            output = mg.generate(case.case_id, store, case.capabilities(),
                                 mg.POLICY_STRONG)
            for kind in ("experiences", "projects"):
                for entity in store.get(kind) or []:
                    for fact in entity.get("facts") or []:
                        if str(fact.get("status")) not in ("denied", "unknown"):
                            continue
                        self.assertIn(str(fact.get("id")), output.skipped_facts,
                                      f"{case.case_id}: denied/unknown fact 未被跳过")
                        for term in fact.get("match_terms") or []:
                            self.assertNotIn(str(term), output.resume_text,
                                             f"{case.case_id}: denied 关键词泄漏")


# --------------------------------------------------------------------------
# 报告落盘
# --------------------------------------------------------------------------

class ReportOutputTests(unittest.TestCase):
    def test_reports_are_written_and_mode_is_marked(self):
        results = [{"case_id": "c-1", "role_family": "ai-fde", "difficulty": "easy",
                    "source": "synthetic", "outcome": rp.OUTCOME_TIE,
                    "decided_by": "judge", "fact_gate": {}, "dimensions": {},
                    "overall": {}, "rounds": []}]
        agg = rp.aggregate(results)
        meta = rp.build_meta({"baseline_version": "v1", "candidate_version": "v2",
                              "mode": "mock"})
        with tempfile.TemporaryDirectory() as tmp:
            json_path, md_path = rp.write_reports(Path(tmp), meta, agg, results)
            self.assertTrue(json_path.is_file())
            self.assertTrue(md_path.is_file())
            self.assertIn("-mock", json_path.name,
                          "mock 报告文件名必须与真实评测报告区分开")
            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("mock 模式", markdown,
                          "mock 报告必须显著标注它不是真实质量结论")


# --------------------------------------------------------------------------
# mock judge 本身
# --------------------------------------------------------------------------

class MockJudgeTests(unittest.TestCase):
    def test_mock_judge_is_deterministic(self):
        payload = {"candidates": {"A": "- **X**：负责 OpenAPI 接入。", "B": "- **X**：负责 OpenAPI 接入。"},
                   "dimensions": list(sc.PAIRWISE_DIMENSIONS),
                   "mock_surfaces": ["OpenAPI"], "mock_ats": ["OpenAPI"]}
        client = MockJudgeClient()
        first = client.complete("s", "u", payload)
        second = client.complete("s", "u", payload)
        self.assertEqual(first, second)
        data = sc.extract_json_object(first)
        self.assertEqual(sc.validate_pairwise_result(data), [])

    def test_mock_judge_needs_payload(self):
        with self.assertRaises(JudgeError):
            MockJudgeClient().complete("s", "u", None)

    def test_mock_judge_output_satisfies_the_declared_schema(self):
        """judge prompt 里声明的输出 schema 必须是可执行的，不是装饰。"""
        if sc.json_schema_status() != "available":
            self.skipTest("jsonschema 未安装")
        payload = {"candidates": {"A": "- **X**：负责 OpenAPI 接入。",
                                  "B": "- **X**：负责 OpenAPI 接入与上线。"},
                   "dimensions": list(sc.PAIRWISE_DIMENSIONS),
                   "mock_surfaces": ["OpenAPI"], "mock_ats": ["OpenAPI", "Webhook"]}
        data = sc.extract_json_object(MockJudgeClient().complete("s", "u", payload))
        problems = sc.validate_json_schema(data, ld.PAIRWISE_RESULT_SCHEMA, "pairwise")
        self.assertEqual(problems, [])


class RunnerIntegrationTests(unittest.TestCase):
    """run_case 端到端：Fact Gate → Pairwise → 结果映射 → 报告字段。"""

    @classmethod
    def setUpClass(cls):
        from evals.quality import run_quality as rq
        cls.rq = rq
        cls.case = ld.discover_benchmark_cases(only=["ai-fde-001"])[0]
        cls.prompts = {
            "pairwise_judge": ld.load_prompt("pairwise_judge"),
            "fact_guard": ld.load_prompt("fact_guard"),
            "evidence_judge": ld.load_prompt("evidence_judge"),
        }
        strong = mg.generate(cls.case.case_id, cls.case.fact_store(),
                             cls.case.capabilities(), mg.POLICY_STRONG)
        weak = mg.generate(cls.case.case_id, cls.case.fact_store(),
                           cls.case.capabilities(), mg.POLICY_WEAK)
        cls.strong = ld.RunOutput(label="x", resume_path=None, facts_path=None,
                                  resume_text=strong.resume_text, facts=strong.facts)
        cls.weak = ld.RunOutput(label="y", resume_path=None, facts_path=None,
                                resume_text=weak.resume_text, facts=weak.facts)

    def _run(self, baseline, candidate, client, **kwargs):
        options = dict(strict_gate=False, semantic_guard=False,
                       evidence_judge=False, seed=0)
        options.update(kwargs)
        return self.rq.run_case(
            self.case, baseline, candidate, mode="real", client=client,
            prompts=self.prompts, **options)

    def test_strong_candidate_beats_weak_baseline(self):
        result = self._run(self.weak, self.strong, LengthBiasJudge())
        self.assertEqual(result["outcome"], rp.OUTCOME_CANDIDATE_WIN)
        self.assertEqual(result["decided_by"], "judge")
        self.assertTrue(result["position_consistency"])
        self.assertGreater(result["evidence_recall"]["candidate"]["recall"],
                           result["evidence_recall"]["baseline"]["recall"])

    def test_position_biased_judge_is_excluded(self):
        result = self._run(self.weak, self.strong, FixedWinnerJudge("A"))
        self.assertEqual(result["outcome"], rp.OUTCOME_POSITION_INCONSISTENT)
        self.assertFalse(result["position_consistency"])

    def test_fact_gate_decides_without_calling_the_judge(self):
        judge = LengthBiasJudge()
        _, broken_resume = fabricated_resume()
        broken = ld.RunOutput(label="y", resume_path=None, facts_path=None,
                              resume_text=broken_resume, facts=None)
        result = self._run(self.weak, broken, judge)
        self.assertEqual(result["outcome"], rp.OUTCOME_CANDIDATE_FACT_FAIL)
        self.assertEqual(result["decided_by"], "fact_gate")
        self.assertEqual(judge.calls, 0, "Gate 已判定时不应再花成本调用 Judge")

    def test_judge_error_is_surfaced_not_swallowed(self):
        result = self._run(self.weak, self.strong, MalformedJudge())
        self.assertEqual(result["outcome"], rp.OUTCOME_JUDGE_ERROR)
        self.assertIn("judge_error", result)

    def test_evidence_judge_attaches_diagnostics(self):
        class EvidenceAndPairwise(JudgeClient):
            name = "combo"

            def complete(self, system, user, payload=None):
                import json
                if "evidence_recall" in system:
                    # 这是 Judge 2 的 prompt，返回证据诊断而不是胜负结论
                    return json.dumps({
                        "requirements": [{"requirement": "API 集成", "importance": "high",
                                          "evidence_exists": True,
                                          "evidence_in_resume": True,
                                          "quality": "strong"}],
                        "available_high_value_evidence": 9,
                        "used_high_value_evidence": 8,
                        "missed_high_value_evidence": [{"fact": "fact-a07", "why": "x"}],
                        "evidence_recall": 0.89,
                    }, ensure_ascii=False)
                candidates = (payload or {}).get("candidates") or {}
                a, b = str(candidates.get("A") or ""), str(candidates.get("B") or "")
                return _result_json("A" if len(a) >= len(b) else "B")

        result = self._run(self.weak, self.strong, EvidenceAndPairwise(),
                           evidence_judge=True)
        self.assertEqual(result["outcome"], rp.OUTCOME_CANDIDATE_WIN)
        entry = result["evidence_judge"]["candidate"]
        self.assertAlmostEqual(entry["evidence_recall"], 0.89)
        self.assertEqual(entry["missed_high_value_evidence"], [{"fact": "fact-a07", "why": "x"}])

    def test_dry_run_makes_no_verdict(self):
        result = self.rq.run_case(
            self.case, self.weak, self.strong, mode="dry-run", client=None,
            prompts={}, strict_gate=False, semantic_guard=False,
            evidence_judge=False, seed=0)
        self.assertEqual(result["outcome"], rp.OUTCOME_DRY_RUN)
        self.assertEqual(result["decided_by"], "dry_run")
        # dry-run 仍然做确定性 Fact Gate 与 Evidence Recall，便于提前发现问题
        self.assertIn("fact_gate", result)
        self.assertIn("evidence_recall", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
