# Recruiter Salience

只负责「人能不能在 10–20 秒内看到最关键 evidence」。
关键词是否被检索到属于 `ats-rules.md`（ATS Coverage），两者必须分开检查。

## 与 ATS Coverage 的区别

| | ATS Coverage | Recruiter Salience |
|---|---|---|
| 面向 | 解析与检索系统 | HR / Hiring Manager |
| 问题 | 关键词是否合理出现、是否可解析 | 核心 evidence 是否足够显眼、足够靠前 |
| 典型失败 | JD 要求的 `FastAPI` 从未出现 | `FastAPI` 出现在第 2 页 |
| 结果 | `ATS Coverage = pass` | `Recruiter Salience = fail` |

同一条简历可以 ATS 通过而 Salience 不通过。两种情况必须分别报告，不能互相替代。

## 检查项

在 10–20 秒内扫过简历时，能否看到：

- 核心匹配点是否出现在前段可见区（个人优势 + 最近一段相关经历）；
- 个人优势是否正面回应 JD 的核心要求，而不是通用素质；
- 最近的相关经历是否承担主要证明责任；
- 最重要的 evidence 是否埋在很靠后、或只出现在弱相关项目里；
- 是否需要把高价值内容前置。

`validate_claims.py` 的 `RECRUITER_SALIENCE_MISSING` 检查：
`relevance_rank` ≤ 3 的 claim 若起始位置超出前段可见区就告警。
这只是粗筛，最终以人工判断为准。

## Runtime Pass 边界

Recruiter Salience 在正常 Runtime 只做一次 pass。只允许局部调整已有 evidence 的顺序、
合并低价值 bullet，或把已有 evidence 前置；不得因一次 salience 提示进入「重写 → duplicate
→ scope → salience」循环。该提示是 heuristic signal，不要求 warning=0。

## 前段可见区

机器判定使用「个人优势 + 最近一段工作经历」；无法解析章节时退化为全文比例兜底。

人工复核的等价标准：**只看上半页，能否回答「这个人为什么适合这个岗位」**。
如果答案需要翻到第二页才出现，就是 Salience 失败。

## 修复手段

| 问题 | 动作 |
|---|---|
| 高相关 evidence 在很靠后的项目 | 前置到个人优势，或在最近相关经历中体现 |
| 个人优势全是通用素质 | 改写为「能力结论 + 证据或规模」，直接回应 JD |
| 最匹配的经历不是最近一段 | 让最近一段承担主要证明责任，或在个人优势中提炼 |
| 关键结果被埋在长段落中 | 拆出独立 bullet，把结果放在句末 |
| 版面被无关内容占满 | 按 `content-selection.md` 压缩或删除 |

前置不等于让每个 claim 都挤进个人优势。个人优势保持 3–4 条，
每条不超过 2 行，只放真正的核心匹配点。

## 禁止

- 为了前置而新增未经确认的技术栈或结果；
- 为了前置而把 `support` 写成 `lead`；
- 把关键词塞进个人优势却不提供任何 evidence。

前置的是**已有的高价值 evidence**，不是措辞。
