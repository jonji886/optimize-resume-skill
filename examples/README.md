# Examples / Smoke Fixtures

端到端场景：真实 JD + Fact Store + 最终简历，用于验证完整链路
（Fact Store → 证据映射 → 改写 → 校验 → lint）是否自洽。

它们同时被 `evals/run_eval.py --suite fixtures` 与 `--suite schema` 使用。

## 目录结构

~~~text
examples/<name>/
├── case.yaml        # 场景说明、JD 原文、期望结果
├── fact-store.yaml  # 该场景的 Fact Store
└── resume.md        # 该场景产出的简历
~~~

## 覆盖场景

| 场景 | 验证重点 |
|---|---|
| `001-ai-solution-engineer` | 直接匹配（OpenAPI / Webhook）与个人项目边界；denied 的「客户需求调研」不得复活 |
| `002-tech-support-to-customer-success` | denied 的续约率 / 满意度不得复活；客户交付经验只能作为可迁移能力表达 |
| `003-presales-unknown-gate` | JD 核心要求中 `Kubernetes` 为 unknown → 正常出简历 + 标注缺口，不阻断 |

## 运行

~~~bash
python3 evals/run_eval.py --suite fixtures
~~~

## 单独校验某个场景

~~~bash
python3 scripts/validate_claims.py examples/001-ai-solution-engineer/fact-store.yaml \
  --resume examples/001-ai-solution-engineer/resume.md

python3 scripts/lint_resume.py examples/001-ai-solution-engineer/resume.md
~~~

## 新增场景

新增目录，放入 `case.yaml`、`fact-store.yaml`、`resume.md` 即可被自动发现。
若是刻意保留 warning 的场景，在 `case.yaml` 中用 `expect_warnings` 声明，
不要留成未解释的告警。
