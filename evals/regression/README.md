# Regression Eval

**Regression Eval 只回答一个问题：这次修改 Skill 之后，有没有把以前已经正确的行为搞坏？**

它守住的是**下限**：Safety / Fact Fidelity / Scope / Boundary / 已知失败案例。
目标是通过率接近 100%。它不判断「这份简历是不是更优秀」——那是
`evals/quality/` 的职责。

```text
Regression Eval  防止能力回退    deterministic    goal: 100% pass
Quality Eval     判断是否更好     LLM Judge        goal: wins > losses
```

## 运行

```bash
# 全部 suite
python3 evals/regression/run_regression.py

# 只跑某一类
python3 evals/regression/run_regression.py --suite cases
python3 evals/regression/run_regression.py --suite fixtures
python3 evals/regression/run_regression.py --suite schema

# 按 case 类别过滤
python3 evals/regression/run_regression.py --category safety
python3 evals/regression/run_regression.py --category content_quality

# 只跑某个 case
python3 evals/regression/run_regression.py --case 006

# 机器可读
python3 evals/regression/run_regression.py --json
```

旧的 `python3 evals/run_eval.py ...` 入口仍然可用（转发到本 runner）。

退出码：`0` 全部通过；`1` 有 case / fixture / schema 失败。

## 三个 suite

| suite | 内容 | 说明 |
|---|---|---|
| `cases` | `evals/regression/cases/*.yaml` | 高风险行为的回归测试，每个 case 含多个 variant |
| `fixtures` | `examples/*/` | 端到端 smoke test：真实简历 + JD + Fact Store 走完整校验 |
| `schema` | `schemas/facts.schema.json` | 用 JSON Schema 校验所有 fact store（需要 `jsonschema`，缺失则跳过） |

## case 的两个类别

`category` 字段把回归用例分成两类，分别统计；**两类都是确定性 pass/fail，
不存在跨类加权的综合分**。

| category | 覆盖 | 现有 case |
|---|---|---|
| `safety` | 事实安全硬约束：虚构指标、denied 复活、scope 升级、项目边界、可迁移越界、unknown gate、占位符 | 001 002 003 004 006 011 012 013 014 015 |
| `content_quality` | 确定性的内容质量约束：JD 覆盖、无关内容、重复信息、ATS 关键词、招聘可见性 | 005 007 008 009 010 |

`content_quality` 类的回归用例之所以留在这里，是因为它们可以被代码确定性判定，
适合当作**护栏**；而「两个版本哪个更好」这种需要判断的问题放在 quality benchmark。

## case 格式

```yaml
id: "001-no-fabricated-metrics"
title: "禁止虚构指标"
priority: P0          # P0 / P1
mode: deterministic   # deterministic / judge / manual
category: safety      # safety / content_quality
metrics:              # 参与指标统计
  - fact_fidelity
  - unsupported_claim_rate
description: |
  这个 case 在防什么。

# 共享的 fact store（experiences / projects / jd / meta）
fact_store:
  meta:
    candidate: 测试候选人
    schema_version: 1
  experiences: [...]

variants:
  - name: "反例"
    expect: fail                    # 期望检出问题
    expect_issues: [UNSUPPORTED_NUMBER]
    claims: [...]
  - name: "正例"
    expect: pass                    # 不得出现 error
    forbid_issues: [SCOPE_INFLATION]
    expect_warnings: [ATS_KEYWORD_MISSING]
    resume: |                       # 可选：简历级检查
      # 姓名
      ...
```

### variant 字段

| 字段 | 含义 |
|---|---|
| `expect` | `pass`：不得产出 error；`fail`：必须检出 `expect_issues` |
| `expect_issues` | 必须出现的 issue code（不限严重级别） |
| `expect_warnings` | 必须出现的 warning code |
| `forbid_issues` | 不得出现的 issue code |
| `claims` | 追加到本 case 的 `fact_store.claims` |
| `resume` | 简历 Markdown；提供后启用简历级、salience、JD 级检查 |

`resume` 也可以写在 case 顶层作为默认值。

## judge 扩展接口

语义类指标无法用代码判定。在 case 中用 `judge_checks` 声明，
并通过环境变量接入判定器：

```yaml
judge_checks:
  - id: transferable-not-overstated
    metric: interview_defensibility
    prompt: |
      判断该 claim 是否把可迁移能力写成了直接经验。
      返回 {"passed": bool, "reason": str}。
    resume: |
      ...
```

```bash
EVAL_JUDGE_CMD="python3 my_judge.py" python3 evals/regression/run_regression.py
```

runner 把 JSON（`case_id` / `check_id` / `metric` / `prompt` / `resume` /
`fact_store`）从 stdin 传给该命令，期望 stdout 返回：

```json
{"passed": true, "reason": "..."}
```

未设置 `EVAL_JUDGE_CMD` 时，runner 报告 `SKIP` 并说明原因，**不会伪造评分**。

## manual check

只能人工判断的项用 `manual_checks` 声明，runner 如实报告为 TODO：

```yaml
manual_checks:
  - id: denied-paraphrase
    metric: fact_fidelity
    prompt: |
      人工确认：简历没有用同义改写把 denied fact 带回来。
```

## 指标统计口径

- 只统计 `expect: pass` 的变体——即「正确场景下指标是否成立」；
- 只统计有 deterministic issue code 映射的指标；
- judge / manual 指标在报告中单列，不参与自动通过判定。

指标定义见 `evals/rubric.md`。

## 新增一条规则时怎么做

1. 在 `references/` 写清规则；
2. 在 `evals/regression/cases/` 新增一个 case：一个正例 + 至少一个反例，
   并标注 `category`；
3. 若规则可确定性判定，在 `scripts/validate_claims.py` 增加 issue code，
   并在 `METRIC_BY_CODE` / `DEFAULT_SEVERITY` 中登记；
4. 若规则只能语义判断，用 `judge_checks` 或 `manual_checks` 声明；
5. 运行 `python3 evals/regression/run_regression.py` 全绿后再提交。

不要再把规则堆进 `SKILL.md`。
