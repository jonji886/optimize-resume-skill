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

## ATS 与 Recruiter Salience

两个独立检查，不互相替代：

| | ATS Coverage | Recruiter Salience |
|---|---|---|
| 面向 | 解析与检索系统 | HR / Hiring Manager |
| 关注 | JD 关键词是否合理出现、格式是否可解析 | 核心 evidence 是否在前段可见区、个人优势是否回应 JD |
| 典型失败 | `FastAPI` 从未出现 | `FastAPI` 出现在第 2 页 |

关键词存在但位置过深时，ATS 通过而 Salience 不通过，必须分别报告。

## Eval 系统

任何规则修改都应该用固定 case 判断变好还是变差。

~~~bash
python3 evals/run_eval.py                 # cases + fixtures + schema
python3 evals/run_eval.py --suite cases   # 15 个高风险回归场景
python3 evals/run_eval.py --json
~~~

- `evals/cases/`：15 个高价值回归 case（虚构指标、项目边界、denied 复活、
  可迁移越界、scope 升级、JD 覆盖、重复信息、ATS、招聘可见性、unknown gate 等）；
- `examples/`：3 个端到端 smoke 场景；
- `evals/rubric.md`：指标定义，标注 deterministic / judge / manual；
- judge 扩展接口：设置 `EVAL_JUDGE_CMD` 接入外部判定器；未设置时如实报告
  `SKIP`，不伪造评分。

## 运行检查

~~~bash
# 事实安全检查（denied/unknown 复活、数字越界、scope 升级、项目边界、JD/ATS 覆盖）
python3 scripts/validate_claims.py "张三-AI解决方案工程师.facts.yaml" \
  --resume "张三-AI解决方案工程师.md"

# 输出格式检查（章节、格式、重复、占位符）
python3 scripts/lint_resume.py "/绝对路径/张三-AI解决方案工程师.md"

# 回归 eval
python3 evals/run_eval.py
~~~

两个脚本职责不重叠：`validate_claims.py` 管事实安全，`lint_resume.py` 管输出格式。

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
│   ├── validate_claims.py        # 事实安全 deterministic 校验
│   └── lint_resume.py            # 输出格式校验
├── evals/
│   ├── README.md
│   ├── rubric.md
│   ├── run_eval.py
│   └── cases/*.yaml
├── examples/
│   └── <scenario>/{case.yaml,fact-store.yaml,resume.md}
└── README.md
~~~

## 新增规则时怎么做

优先新增 `references/` 文档与 `evals/cases/` 用例，
不要把规则继续堆进 `SKILL.md`。判定方式：

- Deterministic problem → 写进 `scripts/validate_claims.py` 并登记 issue code；
- Semantic judgment → 留在 `references/` 由 Agent 判断，或用 judge check 声明。
