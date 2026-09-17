# Eval Rubric

指标定义与判定方式。每个指标标注类型：

- **deterministic**：`scripts/validate_claims.py` 可自动判定，参与 metric 表统计；
- **judge**：需要外部判定器（通过 `EVAL_JUDGE_CMD` 接入）；
- **manual**：只能人工复核，runner 如实报告为 TODO，不伪造评分。

## 指标总览

| 指标 | 中文 | 类型 | 关联 issue code |
|---|---|---|---|
| `fact_fidelity` | Fact Fidelity | deterministic | `DENIED_TERM_REAPPEARS`、`CLAIM_REFERENCES_DENIED_FACT`、`CLAIM_REFERENCES_UNKNOWN_FACT`、`CLAIM_MISSING_FACT_REF`、`RESUME_BULLET_UNVERIFIED`、`RESUME_CLAIM_TEXT_MISMATCH` |
| `unsupported_claim_rate` | Unsupported Claim Rate | deterministic | `UNSUPPORTED_NUMBER`、`CLAIM_WITHOUT_EVIDENCE` |
| `scope_inflation_rate` | Scope Inflation Rate | deterministic | `SCOPE_INFLATION`、`SCOPE_OVERREACH`、`SCOPE_UNVERIFIABLE` |
| `project_boundary_integrity` | Project Boundary Integrity | deterministic | `PROJECT_BOUNDARY_VIOLATION`、`PROJECT_TYPE_MISMATCH`、`PROJECT_DELIVERY_MISMATCH`、`PROJECT_COMMERCIAL_UNVERIFIED` |
| `transferable_discipline` | Transferable Discipline | deterministic | `TRANSFERABLE_AS_DIRECT` |
| `jd_core_coverage` | JD Core Requirement Coverage | deterministic | `JD_CORE_REQUIREMENT_UNCOVERED`、`JD_REQUIREMENT_NO_EVIDENCE` |
| `ats_keyword_coverage` | ATS Keyword Coverage | deterministic | `ATS_KEYWORD_MISSING`、`ATS_KEYWORD_UNSUPPORTED` |
| `recruiter_salience` | Recruiter Salience | deterministic | `RECRUITER_SALIENCE_MISSING` |
| `duplicate_information_rate` | Duplicate Information Rate | deterministic | `RESUME_DUPLICATE_BULLET`、`CLAIM_DUPLICATE_TEXT` |
| `output_hygiene` | Output Hygiene | deterministic | `RESUME_PLACEHOLDER` |
| `relevant_evidence_recall` | Relevant Evidence Recall | judge | — |
| `irrelevant_content_rate` | Irrelevant Content Rate | judge | — |
| `interview_defensibility` | Interview Defensibility | judge / manual | — |

`project_boundary_integrity`、`transferable_discipline`、`output_hygiene` 是对需求中
指标清单的补充拆分：它们对应最高风险的事实安全约束，混在 `fact_fidelity` 里会
失去定位能力。

## 指标定义

### fact_fidelity

简历中所有事实性 Claim 是否都能追溯到 Fact Store，且状态一致。

- `denied` 事实是否通过 claim、同义词或 match_terms 复活；
- `unknown` 事实是否被写成已知能力；
- claim 是否引用了不存在的 fact id；
- 含数字的 bullet 是否有对应 claim 记录；
- claim 文本是否与简历文本漂移。

失败即视为事实安全问题，必须修复。

### unsupported_claim_rate

出现事实性内容（数字、职责层级、结果断言）但缺少 evidence 的 claim 比例。

判定要点：纯表达优化不算失败；一旦出现数字或职责层级就必须有 evidence。

### scope_inflation_rate

claim 使用的动词级别高于 evidence 的 `scope`。

- `support → lead/owner`、`core_execution → lead/owner`：error；
- `support → core_execution`、`lead → owner`：warning；
- evidence 未声明 scope 却使用 lead/owner 级表达：warning，需先确认。

### project_boundary_integrity

个人项目 / Demo / 开源 / 内部工具是否被写成公司项目或商业交付；
未交付项目是否被声称已上线。

`commercial_delivery` 显式 false 时声称商业交付为 error；
未声明时声称商业交付为 warning（要求先确认）。

### transferable_discipline

`transferable` 事实是否被写成直接经验。

判定：claim 的 `claim_type` 为 `direct`（默认）但其全部 evidence 都是
`transferable` → 失败。只能表达为可迁移能力，并写清原场景边界。

### jd_core_coverage

JD 核心要求的覆盖情况。

- 有事实支持但简历与 claim 均未体现 → `JD_CORE_REQUIREMENT_UNCOVERED`（error）；
- 没有事实支持 → `JD_REQUIREMENT_NO_EVIDENCE`（warning），
  这是候选人的真实缺口，不得写入简历，只列入交付说明。

### ats_keyword_coverage

双向检查：

- `ATS_KEYWORD_MISSING`：事实支持但简历完全未出现（覆盖不足）；
- `ATS_KEYWORD_UNSUPPORTED`：简历出现但无事实支持（关键词堆砌）。

两者都是 warning。ATS 与 Recruiter Salience 是独立指标，不互相替代：
关键词存在但位置过深时，本项通过而 `recruiter_salience` 失败。

### recruiter_salience

`relevance_rank` ≤ 3 的 claim 是否落在前段可见区
（个人优势 + 最近一段工作经历）。

机器判定是粗筛，最终按人工 10–20 秒视线判断。

### duplicate_information_rate

同一能力是否被措辞高度重复地证明：重复 bullet、重复 claim。

处理方式见 `references/content-selection.md` 的 Information Uniqueness。

### output_hygiene

最终文件是否残留 TODO、待补充、HTML 注释等内部标记。

### relevant_evidence_recall（judge）

候选人的高价值证据是否都被用上，还是被无关内容挤掉。
需要判断「哪些证据本可以用但没写」。

### irrelevant_content_rate（judge）

简历中与实际证明作用无关的内容占比。

### interview_defensibility（judge / manual）

每条关键表述在面试追问下能否自证：

- 数字能否说明统计口径与来源；
- scope 表述能否对应实际做的事；
- 可迁移表达是否明确承认了场景差异。

## 通过标准

- `expect: pass` 的变体：不得产出任何 error 级 issue；
- `expect: fail` 的变体：必须检出声明的 `expect_issues`；
- `expect_warnings` 声明的 warning 必须出现；
- `forbid_issues` 声明的 code 不得出现；
- fixtures suite：`validate_claims.py` 与 `lint_resume.py` 均 0 error。

judge / manual 指标不参与自动通过判定，但必须在报告中如实列出。
