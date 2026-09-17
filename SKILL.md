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

## Runtime 执行协议

默认采用「快速模式」完成首次优化；只有用户明确要求“完整审查 / 深度审计 / 全面评估”时，才采用「完整审查模式」。两种模式都必须执行事实安全门禁，不得为了提速放宽事实约束。

执行时遵守以下收敛规则：

1. 按阶段单次处理，不在同一阶段反复重建 Fact Store、JD 分析或内容筛选结果；
2. 每个阶段完成后只输出一行进度：`[阶段 x/5] 名称：完成`；进度只报告状态和产物，不复述逐条规则或展开内部推理；
3. 正常流程禁止读取或运行 `evals/`、Quality Benchmark、LLM Judge；
4. `validate_claims.py` 与 `lint_resume.py` 只调用命令，不读取源码并手工模拟检查；工具不可用时如实报告，不用长篇推理替代执行；
5. 校验发现 `ERROR` 后，只针对报错位置修复，最多进行 2 轮修复；达到上限仍有 `ERROR` 时停止并报告阻断原因；
6. `WARNING` 只做一次针对性判断，不触发全量重写；真实问题在当前修复轮次处理，误报集中记录；
7. 两道门禁均无 `ERROR` 后立即交付，不继续进行无界的“再优化”或重复审计。

快速模式的最小阶段为：`Intake → Fact Store/JD 证据矩阵 → 初稿 → 两道 Runtime Guard → 交付`。
完整审查模式在此基础上增加内容筛选、角色映射、ATS 语义自然度、Recruiter Salience 语义审查和面试钩子检查，但仍遵守上述两轮修复上限。

## 资源与加载时机

只在实际需要时读取，不要一次性全部加载。

| 资源 | 何时读取 |
|---|---|
| `references/fact-boundaries.md` | 建立或更新 Fact Store、判断事实能否写入、处理未知信息 |
| `references/jd-analysis.md` | 拆解 JD、建立证据映射、做职业风险扫描 |
| `references/scope-rules.md` | 选择 bullet 动词、判断参与层级 |
| `references/content-selection.md` | 决定保留 / 前置 / 压缩 / 合并 / 删除 |
| `references/rewrite-rules.md` | 起草 bullet、写数据与结果、设计面试钩子 |
| `references/ats-rules.md` | 完整审查，或 Runtime Guard 报告 ATS 警告后做语义判断 |
| `references/recruiter-review.md` | 完整审查，或 Runtime Guard 报告 Salience 警告后做语义判断 |
| `references/output-format.md` | 排版、命名、写文件前 |
| `references/role-mappings.md` | 判断岗位相关性和项目取舍时 |
| `references/examples.md` | 需要改写示例时 |
| `schemas/facts.schema.json` | 需要确认 Fact Store 字段时 |
| `scripts/validate_claims.py` | 步骤 7，只调用命令；事实安全、JD/ATS/Salience 的机械校验 |
| `scripts/lint_resume.py` | 步骤 10，只调用命令；输出格式校验 |

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
- `WARNING` 只做针对性判断：真实问题在当前修复轮次处理，误报在最终说明中注明；不得因 WARNING 重新执行整套流程。

脚本只做 deterministic 检查。只调用脚本，不读取源码并手工模拟其内部实现；语义层面的
「是否过度包装、是否自然、是否值得保留」按执行模式处理。校验脚本已覆盖 JD 核心要求、
ATS 关键词和 Recruiter Salience 的机械检查。

### 8. ATS Coverage 检查

不要重复执行脚本已经完成的关键词存在性和事实支持检查。快速模式只在脚本报告相关
WARNING，或需要判断关键词是否自然时做一次语义复核；完整审查模式读取
`references/ats-rules.md`，检查关键词自然度和上下文准确性。

### 9. Recruiter Salience 检查

不要重复执行脚本已经完成的前段位置检查。快速模式只针对脚本 WARNING 或 JD 前 3
个核心要求做一次语义复核；完整审查模式读取 `references/recruiter-review.md`，确认核心
evidence 出现在前段可见区（个人优势 + 最近一段相关经历），个人优势正面回应 JD。

ATS 与 Salience 是两次独立检查：关键词存在但位置过深时，
ATS 通过、Salience 不通过，必须分别报告。

### 10. 输出格式检查

~~~bash
python3 scripts/lint_resume.py "/绝对路径/张三-{岗位名称}.md"
~~~

修复全部 `ERROR`；`WARN` 按输出规范做一次针对性判断。若需要修复，计入 Runtime 执行协议
中的 2 轮修复，不得单独开启新的全量审计。

### 11. 交付

清理临时文件与内部标记，输出目标文件的可点击路径与说明。

## 工作模式

根据用户意图选择一种，不机械重复完整流程。

| 模式 | 触发 | 行为 |
|---|---|---|
| A 仅诊断 | 用户要求分析、评估、指出问题 | 只输出诊断，不改文件 |
| B 快速首次优化（默认） | 首次为某 JD 生成简历，未明确要求深度审查 | 执行最小阶段；缺口按 claim 级别处理；只做一次语义抽查 |
| C 完整审查 | 用户明确要求完整审查、深度审计或全面评估 | 执行 1–11，并加载所需语义规则；仍受两轮修复上限约束 |
| D 迭代修改 | 要求精简、删除、调整某模块或换格式 | 直接修改目标文件，同步更新 Fact Store 的 claims；检查相邻模块一致性 |

信息是否齐全不是独立模式：已有 Fact Store 或用户已确认事实时，在 B/C 中跳过已完成的
Intake 和事实提取，不重复建立相同事实。

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

## 与 Eval 的边界

正常优化简历时，只执行本文件描述的两道 deterministic 门禁：

~~~text
步骤 7   validate_claims.py   事实安全
步骤 10  lint_resume.py       输出格式
~~~

这两者构成 Runtime Guard：轻量、确定性、可自动运行。**不要**在正常生成流程中调用
LLM Judge 或 Quality Benchmark，也不要读取这些目录中的源码或测试用例——那会带来成本、
延迟与无界推理风险。

Quality Benchmark（`evals/quality/`）属于 development / release 评测，回答的是
「新版本 Skill 是否比旧版本生成了更好的简历」，由人工或 CI 显式触发：

- 需要对比两个 Skill 版本的产出时使用；
- 需要排查某个 case 为什么退化时使用；
- 不要在每次交付简历时运行。

可选的 shadow eval（默认关闭，只有用户明确要求、或 Skill 自身开发调试时才使用）：
在产出简历之后追加一次事实安全复核，用
`python3 evals/quality/run_quality.py --case <case-id> --dry-run` 检查 Judge 输入是否干净、
A/B 顺序是否镜像，或用 `--semantic-fact-guard` 补一次语义层事实安全检查。
它不是交付流程的一部分，也不产出「简历得分」。

修改 `SKILL.md` / `references/` / `scripts/` 之后，改动的验收方式是：

~~~bash
python3 evals/regression/run_regression.py   # 不能有回退，目标 100% 通过
~~~

## 最终回复

提供目标文件的可点击路径，并简要说明：

- 本次保留和强化了什么；
- 删除或压缩了什么；
- 仍有哪些真实缺口需要面试准备（含 `unknown` 导致的未覆盖 JD 要求）；
- 未修复的 `WARNING`（如有）。

只有实际设计了面试钩子时才说明钩子，不机械输出后续建议。
