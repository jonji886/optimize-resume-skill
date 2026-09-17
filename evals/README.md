# Evals

修改 `SKILL.md`、`references/` 或 `scripts/` 之后，需要能回答两个**不同**的问题：

```text
1. Skill 有没有做错？              →  Regression Eval
2. 在没有做错的前提下，新版本有没有做得更好？  →  Quality Benchmark
```

两者不能互相替代，也不能混成一个综合分。这就是本目录拆成两层的原因。

## 三层结构

```text
                    Resume Skill
                         │
                         ↓
                  Optimized Resume
                         │
       ┌─────────────────┼─────────────────┐
       ↓                 ↓                 ↓
 Runtime Guard     Regression Eval    Quality Benchmark
 运行时轻量检查      防止能力回退         判断版本优劣
       │                 │                 │
 deterministic      mostly deterministic   LLM Judge
 (Skill 执行链)      (发布前回归)          (版本对比)
```

| 层 | 位置 | 回答的问题 | 判定方式 | 是否在正常简历生成时运行 |
|---|---|---|---|---|
| Runtime Guard | `scripts/validate_claims.py`、`scripts/lint_resume.py` | 这份简历有没有踩事实安全 / 格式红线 | deterministic，ERROR 即阻断 | **是**（`SKILL.md` 步骤 7 / 10） |
| Regression Eval | `evals/regression/` | 这次改动有没有把以前正确的行为搞坏 | deterministic，pass/fail，目标 100% | 否（开发 / 发布流程） |
| Quality Benchmark | `evals/quality/` | 两个都没做错时，哪个版本生成的简历更有效 | Blind Pairwise Judge + Position Swap | 否（开发 / 发布流程） |

**Quality Judge 默认不进入 Skill 主执行链。** 正常优化简历时只跑 Runtime Guard，
不跑 LLM Judge；否则成本、延迟和 reward hacking 风险都会失控。

## 快速开始

```bash
# 1) 回归：确定性、离线、必须全绿
python3 evals/regression/run_regression.py

# 2) 质量：验证 Harness（离线，不调用真实模型）
python3 evals/quality/run_quality.py --dry-run
python3 evals/quality/run_quality.py --mock

# 3) 质量：版本对比（先准备 evals/quality/runs/<case-id>/ 下的两套产物）
python3 evals/quality/run_quality.py \
  --baseline v0.6 --candidate v0.7 \
  --runs-dir evals/quality/runs

# 4) Harness 自身的测试
python3 -m unittest discover -s evals/tests -t .
```

## 目录结构

```text
evals/
├── README.md                # 本文件：三层结构、边界、运行方式
├── rubric.md                # 指标定义（deterministic / judge / manual）
├── run_eval.py              # 向后兼容入口 → regression/run_regression.py
│
├── regression/
│   ├── README.md
│   ├── run_regression.py
│   └── cases/*.yaml         # safety / content_quality 两类确定性用例
│
├── quality/
│   ├── README.md            # 完整方法论：为什么不用绝对分、为什么必须 A/B swap
│   ├── run_quality.py       # CLI
│   ├── fact_gate.py         # Fact Safety Hard Gate
│   ├── evidence.py          # JD Evidence Recall（deterministic）
│   ├── pairwise.py          # Blind Pairwise + Position Swap
│   ├── mock_generator.py    # 离线产物生成（已知答案自检）
│   ├── render.py            # Judge 输入渲染（干净上下文约束）
│   ├── benchmark/
│   │   ├── benchmark.yaml   # benchmark 版本 / 覆盖 / 门槛
│   │   ├── case.schema.json
│   │   ├── shared/persona-*/{resume.md,facts.yaml}
│   │   └── <role-family>/<case-id>/{case.yaml,jd.md}
│   ├── judges/
│   │   ├── pairwise_judge.md    # Judge 3：盲评 A/B（主）
│   │   ├── fact_guard.md        # Judge 1：语义层事实安全（可选）
│   │   └── evidence_judge.md    # Judge 2：JD evidence 诊断（可选）
│   ├── schemas/*.json
│   └── runs/                    # baseline / candidate 产物（不入库）
│
├── common/                  # 两层共用：loaders / schemas / judge_client / report
├── tests/                   # Harness 自身的测试（离线）
└── reports/                 # 评测报告（不入库）
```

## 设计原则（按优先级）

```text
1. Eval validity        评测必须真的在测它声称在测的东西
2. Reproducibility      同一输入必须给同一结论，版本 / 模型 / prompt / seed 全部记录
3. Fact safety          事实安全是硬门禁，不能被表达质量抵消
4. Ease of adding cases 新增 case = 新增一个目录，不改代码
5. Ease of inspecting failures  报告必须支持 drill-down 到单个 case
6. Simplicity           纯 Python，不引入 Eval 平台或框架
7. Execution speed
```

## 常见误解

- **「跑一次 Eval 得到一个匹配度 87 分」** —— 这不是本体系的核心指标。
  绝对分只作为 diagnostic signal；版本优劣靠盲评 A/B（原因见 `quality/README.md`）。
- **「Quality Eval 失败就说明新版本差」** —— 先看 Fact Gate。一方事实失败时
  根本不会进入质量比较，此时结论是「不安全」，不是「不好」。
- **「Judge 说 B 更好就是 B 更好」** —— Judge 不是 Ground Truth。位置不一致的
  case 会被剔除，且建议按 10%–20% 抽样做人工校准。
