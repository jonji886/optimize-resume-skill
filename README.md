# optimize-resume

证据驱动的中文简历优化 Skill：根据目标岗位 JD 建立 Fact Store、映射证据、
筛选并改写内容，然后做事实安全校验、ATS 覆盖与招聘可见性检查。

## 目标

> 在「不编造事实、不过度扩大职责、面试可自证」的硬约束下，最大化目标 JD
> 核心需求被 ATS 和招聘人员准确识别的概率。

不是生成「看起来最厉害」的简历，而是用最少但最强、最真实、最可验证的
Evidence，让招聘人员最快判断候选人与岗位之间的真实匹配关系。

## 核心架构

~~~text
              ┌──────────────┐
              │ Target JD    │
              └──────┬───────┘
                     │
Resume / Notes → Fact Store
                     │
                     ↓
                JD Evidence Map
                     │
                     ↓
               Content Selector
                     │
                     ↓
                 Rewriter
                     │
                     ↓
              Claim Validator      scripts/validate_claims.py
                     │
                     ↓
                ATS Check          references/ats-rules.md
                     │
                     ↓
          Recruiter Salience Check references/recruiter-review.md
                     │
                     ↓
               Resume Linter       scripts/lint_resume.py
                     │
                     ↓
               Final Resume
~~~

## Fact Store

Facts 与 Presentation 分离。首次为某个 JD 优化时，在简历同目录生成同名 sidecar：

~~~text
张三-AI解决方案工程师.md
张三-AI解决方案工程师.facts.yaml
~~~

字段定义见 `schemas/facts.schema.json`。事实状态：

| 状态 | 含义 | 允许的呈现 |
|---|---|---|
| `confirmed` | 用户明确提供或原始简历已写 | 可以直接作为经验写 |
| `transferable` | 有相近底层经验，场景不同 | 只能表达为可迁移能力 |
| `unknown` | 没有证据 | 禁止生成对应 claim |
| `denied` | 用户明确否认 | 禁止生成，且不得重新引入 |

每条事实还记录 `scope`（参与层级）、`source`（来源）、`numbers`、
`jd_requirements`；项目记录 `type` 与 `commercial_delivery`。

## Evidence-grounded Claims

简历中每条事实性表述都登记在 `claims` 中并指向 fact id：

~~~yaml
- id: claim-001
  text: 负责 OpenAPI、OAuth/Token、Webhook 接入与上线，累计支撑 80+ 应用上线
  evidence: [fact-001, fact-007]
  claim_type: direct
  relevance_rank: 1
~~~

内部约束：**No evidence → no factual claim。**
纯表达优化（「技术服务」→「面向企业客户的技术交付与实施支持」）不需要 fact id。

## Claim-level Gating

未知信息只阻止对应 claim，不默认阻断整份简历：

- `confirmed` → 可以作为直接经验写；
- `transferable` → 只能表达可迁移能力；
- `unknown` → 禁止生成该 claim，其余内容照常优化；
- `denied` → 禁止生成，且后续不得重新引入。

例：JD 要求 `Python / Agent / 客户交付 / Kubernetes`，只有 `Kubernetes` 是 unknown 时，
正确行为是继续产出完整简历并标注该缺口，而不是暂停整份简历。

只有 Critical Missing Information（公司名称、职位、工作起止时间、经历归属、
用户要求新增但真实性无法判断的事实）才允许先阻断并确认。

## 运行模式与停止规则

首次优化默认使用快速模式：`Intake → Fact Store/JD 证据矩阵 → 初稿 → 两道 Runtime Guard → 交付`。
只有用户明确要求完整审查、深度审计或全面评估时，才加载额外语义规则并执行完整审查模式。

运行时遵守以下限制：

- `validate_claims.py` 和 `lint_resume.py` 只调用命令，不读取源码并手工模拟；
- 正常交付不读取或运行 `evals/`、Quality Benchmark、LLM Judge；
- `ERROR` 只针对报错位置修复，最多 2 轮；
- `WARNING` 不触发全量重写，只做一次针对性判断；
- 两道门禁均无 `ERROR` 后立即交付，不继续无限优化。

每个阶段应输出一行进度，进度只报告状态和产物，不复述逐条规则或展开内部推理。若两轮修复后仍有 `ERROR`，停止继续思考并报告阻断原因。

## ATS 与 Recruiter Salience

两个独立检查，不互相替代。`validate_claims.py` 负责其中的机械检查；模型只在快速模式
遇到相关 WARNING，或完整审查模式下补充关键词自然度和前段可见性的语义判断。

| | ATS Coverage | Recruiter Salience |
|---|---|---|
| 面向 | 解析与检索系统 | HR / Hiring Manager |
| 关注 | JD 关键词是否合理出现、格式是否可解析 | 核心 evidence 是否在前段可见区、个人优势是否回应 JD |
| 典型失败 | `FastAPI` 从未出现 | `FastAPI` 出现在第 2 页 |

关键词存在但位置过深时，ATS 通过而 Salience 不通过，必须分别报告。

## Eval 系统

Eval 要回答两个**不同**的问题，因此拆成两层：

~~~text
1. Skill 有没有做错？                    → Regression Eval（守下限）
2. 都没做错时，新版本有没有做得更好？     → Quality Benchmark（判上限）
~~~

~~~bash
# 回归：确定性、离线、必须全绿（38 个 variant + 3 个 fixture + schema）
python3 evals/regression/run_regression.py
python3 evals/regression/run_regression.py --category safety   # 事实安全类
python3 evals/run_eval.py                                      # 兼容旧入口

# 质量：版本对比（离线自检不需要 API Key）
python3 evals/quality/run_quality.py --dry-run
python3 evals/quality/run_quality.py --mock --baseline v0.6 --candidate v0.7
python3 evals/quality/run_quality.py --baseline v0.6 --candidate v0.7 \
  --runs-dir evals/quality/runs

# Harness 自身的测试
python3 -m unittest discover -s evals/tests -t .
~~~

- `evals/regression/cases/`：15 个确定性回归 case，分 `safety`（事实安全硬约束）
  与 `content_quality`（JD 覆盖、ATS、salience、重复信息）两类；
- `evals/quality/benchmark/`：12 个跨岗位 Golden Case，覆盖 8 个 role family；
- `evals/quality/judges/`：`pairwise_judge`（盲评 A/B）、`fact_guard`（语义层事实安全）、
  `evidence_judge`（JD evidence 诊断）；
- `evals/rubric.md`：指标定义，标注 deterministic / judge / manual；
- judge 扩展接口：回归用 `EVAL_JUDGE_CMD`，质量为 `QUALITY_JUDGE_PROVIDER`；
  未设置时如实报告 `SKIP` / 使用 mock，不伪造评分。

三层职责与边界（Runtime Guard / Regression / Quality）见 `evals/README.md`。

### 为什么质量评测不用「匹配度 0～100」

绝对分跨 case 不可比较、每次调用会漂移、84 与 87 的差异无法解释。版本优劣采用
**盲评 A/B**：同一份事实、同一份 JD，两个版本各出一份简历，由不知道版本来源的
Judge 判定谁更好；每个 case 跑两轮镜像顺序（`A=旧/B=新` 与 `A=新/B=旧`）以检测位置偏差，
结论翻转的 case 会被标记 `POSITION_INCONSISTENT` 并排除出 Win Rate。

事实安全是 **Hard Gate**：出现无证据断言、虚构指标、scope 夸大、项目边界越界或
denied 事实复活，该版本在本 case 直接判负，不能被「表达更好」抵消。

## 运行检查

~~~bash
# 事实安全检查（denied/unknown 复活、数字越界、scope 升级、项目边界、JD/ATS 覆盖）
python3 scripts/validate_claims.py "张三-AI解决方案工程师.facts.yaml" \
  --resume "张三-AI解决方案工程师.md"

# 输出格式检查（章节、格式、重复、占位符）
python3 scripts/lint_resume.py "/绝对路径/张三-AI解决方案工程师.md"

# 回归 eval（仅开发 / 发布验收，不属于正常简历生成流程）
python3 evals/regression/run_regression.py
~~~

两个脚本职责不同：`validate_claims.py` 管事实安全及 JD/ATS/Salience 的机械校验，
`lint_resume.py` 管输出格式。
它们构成 Resume Skill 正常执行时的 **Runtime Guard**（`SKILL.md` 步骤 7 / 10），
正常生成简历时只跑这两层，不跑 LLM Judge。

## 使用

提供目标岗位 JD 与原始简历或现有目标简历，然后调用：

~~~
$optimize-resume
~~~

信息完整或修改现有简历时直接更新文件；只有出现 Critical Missing Information
才会先提问。默认输出 `姓名-{岗位名称}.md` 与同名 `.facts.yaml`。

## 文件结构

~~~text
optimize-resume/
├── SKILL.md                      # orchestrator：流程、硬约束、资源加载时机
├── agents/openai.yaml
├── references/
│   ├── fact-boundaries.md        # Fact Store、状态、单一事实源、claim gate
│   ├── jd-analysis.md            # JD 拆解、证据映射、语义对齐、职业风险
│   ├── scope-rules.md            # 参与层级与升级判定
│   ├── content-selection.md      # Layer1/Layer2 评分、Uniqueness、五种动作
│   ├── rewrite-rules.md          # 语言、数据、面试钩子
│   ├── ats-rules.md              # ATS Coverage
│   ├── recruiter-review.md       # Recruiter Salience
│   ├── output-format.md          # 结构与格式
│   ├── role-mappings.md          # 岗位内容映射（按需加载）
│   └── examples.md               # 改写示例
├── schemas/facts.schema.json
├── scripts/
│   ├── validate_claims.py        # 事实安全 deterministic 校验（Runtime Guard）
│   └── lint_resume.py            # 输出格式校验（Runtime Guard）
├── evals/
│   ├── README.md                 # 三层结构、边界、运行方式
│   ├── rubric.md                 # 指标定义
│   ├── run_eval.py               # 兼容入口 → regression/
│   ├── regression/
│   │   ├── run_regression.py
│   │   └── cases/*.yaml          # safety / content_quality 两类确定性用例
│   ├── quality/
│   │   ├── run_quality.py        # 版本对比 CLI（--dry-run / --mock / 真实 Judge）
│   │   ├── fact_gate.py          # Fact Safety Hard Gate
│   │   ├── evidence.py           # JD Evidence Recall
│   │   ├── pairwise.py           # Blind Pairwise + Position Swap
│   │   ├── benchmark/            # Golden Benchmark（12 case / 8 role family）
│   │   ├── judges/*.md           # Judge prompts（带版本号）
│   │   └── runs/                 # baseline / candidate 产物（不入库）
│   ├── common/                   # loaders / schemas / judge_client / report
│   ├── tests/                    # Harness 自身的测试（离线）
│   └── reports/                  # 评测报告 JSON + Markdown（不入库）
├── examples/
│   └── <scenario>/{case.yaml,fact-store.yaml,resume.md}
└── README.md
~~~

## 新增规则时怎么做

优先新增 `references/` 文档与回归用例，不要把规则继续堆进 `SKILL.md`。判定方式：

- Deterministic problem → 写进 `scripts/validate_claims.py` 并登记 issue code，
  在 `evals/regression/cases/` 加一个正例 + 反例；
- 属于事实安全 → 同时确认它在 `evals/quality/fact_gate.py` 的 `GATE_CATEGORY_BY_CODE`
  里，保证版本对比时会被硬门禁拦住；
- Semantic judgment → 留在 `references/` 由 Agent 判断，或用 judge check 声明；
- 影响版本优劣判断的标准 → 改 `evals/quality/judges/pairwise_judge.md` 并**升版本号**，
  否则历史报告不可比较。
