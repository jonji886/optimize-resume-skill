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

## Execution Budget & Termination Rules

默认使用 `FAST_RUNTIME`：日常 JD 定向优化的每个主要阶段只执行一次；只有用户明确要求
严格事实审核、Skill 调试、Benchmark / Regression 或 release 验收时才使用
`STRICT / AUDIT_RUNTIME`。严格模式可以增加复核，但不取消预算。

默认预算为：`draft_count = 1`、`validator_runs <= 3`、`repair_rounds <= 2`、ATS pass 一次、
Recruiter Salience pass 一次、lint 一次。完整状态、指标和停止条件见
[`references/runtime-protocol.md`](references/runtime-protocol.md)。

必须遵守：

1. `Fact Store → JD Evidence Mapping → Content Selection → Draft → Claims → Deterministic
   Validation → Targeted Repair → ATS / Salience → Lint → Deliver`；没有新 evidence 不得回跳
   到事实提取、JD 解析或内容筛选；
2. Draft 先完整生成，再实际运行 validator。不得在 Draft / Rewrite 阶段人工模拟正则、scope
   detector、duplicate threshold 或 ATS detector；validator 才是检测反馈源；
3. `ERROR` 是 blocking，必须局部修复并重新验证；两轮后仍有 ERROR 就停止自动修改并报告
   `unresolved ERROR`；
4. `WARNING` 是 non-blocking heuristic signal，不是 reward function，也不是 Runtime Success
   Criterion；绝不以 `0 WARNING` 为目标。只修复明显事实误导、核心 JD Evidence、显著重复或
   Recruiter 理解受损的问题，其余 warning 可以保留并报告；
5. Targeted Repair 只能遵循 `validator issue → locate affected claim/bullet → local patch →
   validator`，不得触发全量 rewrite；同一 issue 连续两轮仍在时停止继续改写；
6. 正常 Runtime 不读取或运行 `evals/quality/`、Quality Benchmark 或 LLM Judge；
7. 每个阶段最多输出一行进度，不复述内部推理。`runtime_pass` 由 remaining ERROR 和 lint ERROR
   决定，WARNING 可以大于 0。

## 资源与加载时机

只在实际需要时读取，不要一次性全部加载。

| 资源 | 何时读取 |
|---|---|
| `references/fact-boundaries.md` | 建立或更新 Fact Store、判断事实能否写入、处理未知信息 |
| `references/runtime-protocol.md` | 需要确认 Runtime 状态、预算、修复边界或终止条件时 |
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
| `scripts/validate_claims.py` | STATE 7，只调用命令；事实安全、JD/ATS/Salience 的机械校验 |
| `scripts/lint_resume.py` | STATE 10，只调用命令；输出格式校验 |

角色映射按需加载：先识别目标 Role，再读取 `role-mappings.md` 中对应段落，
不要把全部岗位规则读进上下文。

## 输入

- 目标岗位 JD；
- 原始简历或现有目标简历；
- 用户在当前及前序对话中确认、否认或补充的事实。

缺少 JD 或简历且无法从上下文取得时，再向用户补齐。

## 执行流程

### STATE 1 — Intake

确认目标岗位、输入文件、已有事实状态。判断工作模式（见下）。
只处理 Critical Missing Information；其余缺口留到 JD Evidence Mapping / Claims 阶段按 claim
级别处理。

### STATE 2 — Fact Extraction / Fact Store

读取 `references/fact-boundaries.md`，生成 sidecar 文件：

~~~text
张三-{岗位名称}.md
张三-{岗位名称}.facts.yaml
~~~

要求：

- 每条 fact 标注 `status`（confirmed / denied / unknown / transferable）、`scope`、`source`；
- 项目类 fact 所在 project 必须声明 `type` 与 `commercial_delivery`；
- 迭代修改时在原有 Fact Store 上增量更新，不要重建后丢掉已否认事实。

### STATE 3 — JD Requirement Mapping

读取 `references/jd-analysis.md`，把结果写入 Fact Store 的 `jd` 段：

- `core_requirements`：3–5 条核心要求；
- `ats_keywords`：需要被检索的真实技术 / 产品 / 职责名词；
- 职级、行业、必备项与加分项。

同时完成职业风险扫描，结论用于最终说明，不写进简历；把每条核心要求映射到直接匹配、
可迁移或无证据，不为无证据要求新增事实。

### STATE 4 — Content Selection

读取 `references/content-selection.md`，一次性为每条内容产出动作：
`KEEP_AND_PRIORITIZE` / `KEEP` / `COMPRESS` / `MERGE` / `REMOVE`。

重点只做一次 Evidence Selection；duplicate 检测交给 validator，不在写每条 bullet 时模拟。

### STATE 5 — Draft

读取 `references/scope-rules.md` 与 `references/rewrite-rules.md`，按
`references/output-format.md` 的结构写入目标文件。

只遵守已确定的 Fact / JD Evidence Map；Scope 在 Fact Extraction 阶段确定后默认冻结。
先生成完整 Resume，不为了预测 validator 而反复换词。

### STATE 6 — Claims

每条事实性 bullet 同步登记到 Fact Store 的 `claims`：

- `evidence`：支撑它的 fact id；
- `claim_type`：`direct` 或 `transferable`；
- `relevance_rank`：对目标 JD 的重要性，1 最高。

### STATE 7 — Deterministic Validation

~~~bash
python3 scripts/validate_claims.py "张三-{岗位名称}.facts.yaml" \
  --resume "张三-{岗位名称}.md"
~~~

- `ERROR` 必须局部修复后重新运行；最多 2 轮 repair；
- `WARNING` 只做一次针对性判断，不得因 warning 重启整套流程，也不要求 warning=0。

脚本只做 deterministic 检查。只调用脚本，不读取源码并手工模拟其内部实现；语义层面的
「是否过度包装、是否自然、是否值得保留」按执行模式处理。校验脚本已覆盖 JD 核心要求、
ATS 关键词和 Recruiter Salience 的机械检查。

### STATE 8 — Targeted Repair

只处理真实 validator 输出中 blocking ERROR 和少量高价值 WARNING。每轮只定位受影响的
claim / bullet 并局部修改；不全量 Rewrite。两轮后仍有 ERROR 时停止并报告 unresolved ERROR。

### STATE 9 — ATS Coverage / Recruiter Salience

ATS 与 Salience 各只做一次。ATS 仅在已有真实 evidence 时局部补充自然关键词；Salience
仅局部前置、排序或合并已有 evidence。两者都不得触发 scope / duplicate / 全局重写循环。
如修改 claim 文本，必须在剩余 validator 预算内复验；预算用尽则不改事实措辞，只报告风险。

快速模式只在 validator 报告相关 warning 时读取对应参考；完整审查模式再读取
`references/ats-rules.md` 与 `references/recruiter-review.md` 做一次语义复核。

ATS 与 Salience 是两次独立检查：关键词存在但位置过深时，
ATS 通过、Salience 不通过，必须分别报告。

### STATE 10 — Lint

~~~bash
python3 scripts/lint_resume.py "/绝对路径/张三-{岗位名称}.md"
~~~

修复 `ERROR`；`WARN` 只做一次判断，修复计入同一 Runtime 的 2 轮预算，不得开启新的全量审计。

### STATE 11 — Deliver

清理临时文件与内部标记，输出目标文件的可点击路径与说明。

## 工作模式

根据用户意图选择一种，不机械重复完整流程。

| 模式 | 触发 | 行为 |
|---|---|---|
| A 仅诊断 | 用户要求分析、评估、指出问题 | 只输出诊断，不改文件 |
| B `FAST_RUNTIME`（默认） | 首次为某 JD 生成简历，未明确要求深度审查 | 依次执行有界状态；每阶段一次，warning 非阻断，最多 2 轮局部修复 |
| C `STRICT / AUDIT_RUNTIME` | 用户明确要求完整审查、深度审计或全面评估 | 执行 STATE 1–11，并加载所需语义规则；仍受同一预算约束 |
| D 迭代修改 | 要求精简、删除、调整某模块或换格式 | 直接做局部修改，同步更新 claims；仍执行对应 Runtime Guard，不重开无关阶段 |

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
STATE 7   validate_claims.py   事实安全
STATE 10  lint_resume.py       输出格式
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
