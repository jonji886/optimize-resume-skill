# 事实边界与 Fact Store

事实安全相关规则的唯一来源。改写前必读。

## 1. 第一性原理

> 在「不编造事实、不过度扩大职责、面试可自证」的硬约束下，最大化目标 JD
> 核心需求被 ATS 和招聘人员准确识别的概率。

当「更匹配 JD」和「事实准确」冲突时，永远选择事实准确。

## 2. Facts 与 Presentation 分离

~~~text
Input Resume / User Notes
          ↓
       Fact Store        ← 事实账本，唯一事实来源
          ↓
      Evidence Map       ← JD 要求 → fact id
          ↓
    Resume Claims        ← 简历里实际写出的每一条事实性表述
          ↓
      Presentation       ← 措辞、顺序、格式
~~~

Fact Store 只服务三件事：事实边界、Evidence 追溯、Claim 校验。不要扩成通用数据库。

## 3. Fact Store 文件

首次为某个 JD 优化时，在简历同目录生成同名 sidecar：

~~~text
张三-AI解决方案工程师.md
张三-AI解决方案工程师.facts.yaml
~~~

字段定义见 `schemas/facts.schema.json`。核心结构：

~~~yaml
meta:
  candidate: 张三
  target_role: AI解决方案工程师
  schema_version: 1
jd:
  core_requirements: [Agent, API 集成, 客户交付, Python]
  ats_keywords: [Python, FastAPI, Agent, OpenAPI]
experiences:
  - id: qunhe
    employer: 杭州群核信息技术有限公司
    role: 技术服务工程师
    type: work
    facts:
      - id: fact-001
        statement: 支撑 80+ 应用上线
        status: confirmed
        scope: core_execution
        source: original_resume
        jd_requirements: [API 集成, 客户交付]
projects:
  - id: ai-support-delivery
    name: AI 技术支持工作台
    type: personal_project
    commercial_delivery: false
    facts:
      - id: fact-101
        statement: 使用 RAG 和 Agent 构建 AI 技术支持工作台
        status: confirmed
        scope: owner
        source: user_note
claims:
  - id: claim-001
    text: 负责 OpenAPI、OAuth/Token、Webhook 接入与上线，累计支撑 80+ 应用上线
    evidence: [fact-001, fact-007]
    relevance_rank: 1
~~~

`commercial_delivery` 必须显式声明。未声明时不得声称商业交付。

## 4. 事实状态与允许的呈现方式

| 状态 | 含义 | 允许的呈现 |
|---|---|---|
| `confirmed` | 用户明确提供或原始简历已写 | 可以作为直接经验写入 |
| `transferable` | 有相近底层经验，场景不同 | 只能表达为可迁移能力，并写明边界 |
| `unknown` | 没有证据 | **禁止生成对应 claim**，但不阻断整份简历 |
| `denied` | 用户明确否认 | **禁止生成**，且后续任何版本不得重新引入 |

来源 `source`：

- `original_resume` / `user_confirmation` / `user_correction` / `user_note`：可以作为 confirmed 依据；
- `inferred`：只能配合 `unknown` 或 `transferable`，不得作为 confirmed 依据。

## 5. 单一事实源

简历中新增的事实性 Claim 必须来自 Fact Store，包括：

- 数字、比例、金额、客户数量、上线数量；
- 项目结果、业务结果；
- 工作职责、参与层级；
- 技术经验、技术栈；
- 项目类型（个人 / 公司 / 内部工具 / Demo）；
- 是否商业化、是否上线、是否主导。

**不得从 JD 反向生成事实。** JD 说「需要支付 API 经验」不等于候选人有过支付 API 经验。

## 6. Claim → Evidence Trace

每条事实性 claim 在 `claims` 中有记录，并指向支撑它的 fact id。

> No evidence → no factual claim.

纯表达优化（例如「技术服务」→「面向企业客户的技术交付与实施支持」）不需要建立
fact id，`evidence` 可以留空；但一旦出现数字、职责层级或结果断言，就必须有 evidence。

输出给用户时不展示 evidence id，只用于内部校验。

## 7. 禁止的包装（硬约束）

| 禁止 | 正确做法 |
|---|---|
| 个人项目 → 公司项目 | 写明个人项目 / 原型，或作为可迁移能力 |
| Demo / 内部工具 → 商业交付 | 写交付物、覆盖场景、验证结论 |
| 技术支持 → 客户成功 | 写问题拆解、需求回流、上线验证 |
| 协作 → 团队管理 | 写清承担的具体模块与接口边界 |
| 参与 / 支持 → 主导 / Owner | 按 `scope-rules.md` 选词 |
| 未知数字 → 确定数字 | 用可验证的状态变化、覆盖范围或交付物 |
| transferable → 直接经验 | 加「可迁移至…」并写清原场景 |
| denied → 换个说法带回 | 只写已证实的底层能力 |
| 用过某工具 → 做过该方向项目 | 写实际动作与产出 |

## 8. Claim-level Gate

未知信息只阻止对应 claim，不默认阻断整份简历。

~~~text
confirmed      → 可以作为直接经验写
transferable   → 只能表达可迁移能力，不得包装成直接经验
unknown        → 禁止生成该事实 Claim
denied         → 禁止生成，且不得在后续版本重新引入
~~~

示例：JD 要求 `Python / Agent / 客户交付 / Kubernetes`，
其中前三项 confirmed、`Kubernetes` unknown。

- 正确：继续优化整份简历，不写 Kubernetes，在交付说明中标注该缺口。
- 错误：因为 Kubernetes 未知而暂停整份简历生成。

### 只有 Critical Missing Information 才允许阻断

- 无法确认候选人公司名称；
- 无法确认职位或工作起止时间；
- 无法判断输入内容中哪些经历属于候选人；
- 用户明确要求新增一个事实，但真实性无法判断。

除此之外一律 **best effort + claim-level blocking**：先出可用简历，再列出待确认项。

## 9. 交付前用脚本校验

~~~bash
python3 scripts/validate_claims.py "/绝对路径/张三-AI解决方案工程师.facts.yaml" \
  --resume "/绝对路径/张三-AI解决方案工程师.md"
~~~

脚本只做 deterministic 检查（denied/unknown 复活、数字越界、scope 升级、项目边界、
重复、占位符、JD 与 ATS 覆盖）。语义层面的「是否过度包装、是否自然」仍需自行判断，
见 `rewrite-rules.md`。
