---
name: optimize-resume
description: 基于目标 JD 做证据驱动的简历优化：建立 Fact Store、映射 JD 证据、筛选与改写内容、做事实安全校验与 ATS/招聘可见性检查，并生成或迭代更新“姓名-{岗位名称}.md”。用户要求简历优化、JD 匹配、改写现有简历、精简个人优势、筛选项目、检查夸大或调整简历结构时使用。
---
# 简历优化

以猎头筛选和业务面试官追问两个视角工作。优先保证事实可信、岗位相关和表达具体。

## 第一性原理

> 在「不编造事实、不过度扩大职责、面试可自证」的硬约束下，最大化目标 JD
> 核心需求被 ATS 和招聘人员准确识别的概率。

决策优先级，冲突时取前者：

~~~text
1. Fact correctness
2. No scope inflation
3. Interview defensibility
4. JD relevance
5. Recruiter readability
6. ATS compatibility
7. Conciseness
8. Visual polish
~~~

## 永远不能违反的硬约束

1. 简历中事实性 Claim 必须来自 Fact Store，不得从 JD 反向生成；
2. `denied` 事实不得写入，且后续任何版本不得重新引入；
3. `unknown` 事实不得写入，但不阻断整份简历；
4. `transferable` 只能表达为可迁移能力，不得写成直接经验；
5. 参与层级不得高于 fact 的 `scope`；
6. 个人项目 / Demo / 内部工具不得写成公司项目或商业交付；
7. 数字必须有 evidence 支持，未确认指标不得写成确定数字。

完整规则与禁止清单见 `references/fact-boundaries.md`。

## 资源与加载时机

只在实际需要时读取，不要一次性全部加载。

| 资源 | 何时读取 |
|---|---|
| `references/fact-boundaries.md` | 建立或更新 Fact Store、判断事实能否写入、处理未知信息 |
| `references/jd-analysis.md` | 拆解 JD、建立证据映射、做职业风险扫描 |
| `references/scope-rules.md` | 选择 bullet 动词、判断参与层级 |
| `references/content-selection.md` | 决定保留 / 前置 / 压缩 / 合并 / 删除 |
| `references/rewrite-rules.md` | 起草 bullet、写数据与结果、设计面试钩子 |
| `references/ats-rules.md` | ATS Coverage 检查（关键词是否可被解析检索） |
| `references/recruiter-review.md` | Recruiter Salience 检查（核心 evidence 是否够显眼） |
| `references/output-format.md` | 排版、命名、写文件前 |
| `references/role-mappings.md` | 判断岗位相关性和项目取舍时 |
| `references/examples.md` | 需要改写示例时 |
| `schemas/facts.schema.json` | 需要确认 Fact Store 字段时 |
| `scripts/validate_claims.py` | 步骤 7，事实安全校验 |
| `scripts/lint_resume.py` | 步骤 10，输出格式校验 |

角色映射按需加载：先识别目标 Role，再读取 `role-mappings.md` 中对应段落，
不要把全部岗位规则读进上下文。

## 输入

- 目标岗位 JD；
- 原始简历或现有目标简历；
- 用户在当前及前序对话中确认、否认或补充的事实。

缺少 JD 或简历且无法从上下文取得时，再向用户补齐。

## 执行流程

### 1. Intake

确认目标岗位、输入文件、已有事实状态。判断工作模式（见下）。
只处理 Critical Missing Information；其余缺口留到步骤 4 之后按 claim 级别处理。

### 2. 建立 / 更新 Fact Store

读取 `references/fact-boundaries.md`，生成 sidecar 文件：

~~~text
张三-{岗位名称}.md
张三-{岗位名称}.facts.yaml
~~~

要求：

- 每条 fact 标注 `status`（confirmed / denied / unknown / transferable）、`scope`、`source`；
- 项目类 fact 所在 project 必须声明 `type` 与 `commercial_delivery`；
- 迭代修改时在原有 Fact Store 上增量更新，不要重建后丢掉已否认事实。

### 3. 拆解 JD

读取 `references/jd-analysis.md`，把结果写入 Fact Store 的 `jd` 段：

- `core_requirements`：3–5 条核心要求；
- `ats_keywords`：需要被检索的真实技术 / 产品 / 职责名词；
- 职级、行业、必备项与加分项。

同时完成职业风险扫描，结论用于最终说明，不写进简历。

### 4. 映射 JD → Evidence

逐条为 JD 核心要求寻找证据，标记为直接匹配 / 可迁移 / 无证据：

- 直接匹配：回写到对应 fact 的 `jd_requirements`；
- 可迁移：保留 `transferable` 状态，并准备迁移表达；
- 无证据：记为缺口，**不得为此新增事实**。

### 5. 内容筛选

读取 `references/content-selection.md`，对每条内容产出动作：
`KEEP_AND_PRIORITIZE` / `KEEP` / `COMPRESS` / `MERGE` / `REMOVE`。

重点检查 Information Uniqueness：同一能力不要被个人优势、工作经历、项目经历重复证明。

### 6. 改写简历

读取 `references/scope-rules.md` 与 `references/rewrite-rules.md`，按
`references/output-format.md` 的结构写入目标文件。

每条事实性 bullet 同步登记到 Fact Store 的 `claims`：

- `evidence`：支撑它的 fact id；
- `claim_type`：`direct` 或 `transferable`；
- `relevance_rank`：对目标 JD 的重要性，1 最高。

### 7. 校验 Claims（事实安全门禁）

~~~bash
python3 scripts/validate_claims.py "张三-{岗位名称}.facts.yaml" \
  --resume "张三-{岗位名称}.md"
~~~

- `ERROR` 必须全部修复后重新运行，不得交付；
- `WARNING` 逐条判断：真实问题就修复，误报则在最终说明中注明。

脚本只做 deterministic 检查。语义层面的「是否过度包装、是否自然、是否值得保留」
仍需自行判断。

### 8. ATS Coverage 检查

读取 `references/ats-rules.md`。确认 JD 核心关键词有事实支持且合理出现，
且没有出现无事实支持的关键词（关键词堆砌）。

### 9. Recruiter Salience 检查

读取 `references/recruiter-review.md`。确认核心 evidence 出现在前段可见区
（个人优势 + 最近一段相关经历），个人优势正面回应 JD。

ATS 与 Salience 是两次独立检查：关键词存在但位置过深时，
ATS 通过、Salience 不通过，必须分别报告。

### 10. 输出格式检查

~~~bash
python3 scripts/lint_resume.py "/绝对路径/张三-{岗位名称}.md"
~~~

修复全部 `ERROR`；`WARN` 按输出规范判断。

### 11. 交付

清理临时文件与内部标记，输出目标文件的可点击路径与说明。

## 工作模式

根据用户意图选择一种，不机械重复完整流程。

| 模式 | 触发 | 行为 |
|---|---|---|
| A 仅诊断 | 用户要求分析、评估、指出问题 | 只输出诊断，不改文件 |
| B 首次优化存在缺口 | 首次为某 JD 生成简历 | 完整执行 1–11；缺口按 claim 级别处理 |
| C 信息足够 | 事实齐全或用户已回答问题 | 执行 2–11，不重复诊断 |
| D 迭代修改 | 要求精简、删除、调整某模块或换格式 | 直接修改目标文件，同步更新 Fact Store 的 claims；检查相邻模块一致性 |

## Claim-level Gate 与阻断

未知信息只阻止对应 claim：

~~~text
confirmed      → 可以作为直接经验写
transferable   → 只能表达可迁移能力，不得包装成直接经验
unknown        → 禁止生成该事实 Claim
denied         → 禁止生成，且不得在后续版本重新引入
~~~

例：JD 要求 `Python / Agent / 客户交付 / Kubernetes`，
前三项 confirmed、`Kubernetes` unknown。

- 正确：继续优化整份简历，不写 Kubernetes，在交付说明中标注该缺口。
- 错误：因为 Kubernetes 未知而暂停整份简历生成。

只有以下 Critical Missing Information 才允许阻断并先向用户确认
（最多 5 个问题，说明每题补充的是 ATS 证据、scope 事实还是职业风险解释）：

- 无法确认候选人公司名称；
- 无法确认职位或工作起止时间；
- 无法判断输入内容中哪些经历属于候选人；
- 用户明确要求新增一个事实，但真实性无法判断。

其余情况一律 best effort + claim-level blocking。

## 交付前检查

1. `validate_claims.py` 无 `ERROR`；
2. `lint_resume.py` 无 `ERROR`；
3. JD 前 3 个核心要求都有真实证据，或明确标为可迁移 / 缺口；
4. ATS Coverage 与 Recruiter Salience 分别通过或有明确说明；
5. 个人优势与工作经历、项目经历之间没有机械重复；
6. 参与层级、日期、工作年限与 Fact Store 一致；
7. 最终文件无 TODO、scope 注释或其他内部标记。

## 最终回复

提供目标文件的可点击路径，并简要说明：

- 本次保留和强化了什么；
- 删除或压缩了什么；
- 仍有哪些真实缺口需要面试准备（含 `unknown` 导致的未覆盖 JD 要求）；
- 未修复的 `WARNING`（如有）。

只有实际设计了面试钩子时才说明钩子，不机械输出后续建议。
