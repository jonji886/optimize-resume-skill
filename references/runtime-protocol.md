# Runtime 执行协议

本文件是正常简历优化 Runtime 的单一执行协议。`SKILL.md` 负责路由和硬约束，本文负责
状态、预算、反馈分级与终止条件。它不改变 `fact-boundaries.md` 的事实安全规则，也不替代
`validate_claims.py` 的确定性判断。

## 默认模式：FAST_RUNTIME

用于日常 JD 定向简历优化。`STRICT / AUDIT_RUNTIME` 只在用户明确要求严格事实审核、Skill
开发调试、Benchmark / Regression、怀疑存在事实越界或 release 前验证时启用；严格模式可以
增加人工复核，但不取消下列预算，也不能无限循环。

默认预算：

| 项目 | 上限 | 说明 |
|---|---:|---|
| 完整初稿 `draft_count` | 1 | 初稿一次生成完整 Resume + Claims |
| validator runs | 3 | 初次运行 + 最多 2 次修复后复验 |
| `repair_rounds` | 2 | 每轮只能处理上一次真实输出 |
| ATS pass | 1 | 只允许已有 evidence 的局部补词 |
| Recruiter Salience pass | 1 | 只允许已有 evidence 的局部排序 / 前置 |
| lint runs | 1 | WARN 不触发新的审计循环 |

## 有界状态机

正常流程按以下顺序推进：

```text
Intake
→ Fact Extraction（确定 scope，并冻结）
→ JD Requirement Mapping
→ Content Selection（一次主要选择）
→ Draft（完整初稿）
→ Claims（登记 Evidence）
→ Deterministic Validation
→ Targeted Repair（最多 2 轮，必要时回到 Validation）
→ ATS pass（一次）
→ Recruiter Salience pass（一次）
→ Lint
→ Deliver
```

没有新原始证据、用户纠正或 Fact Store 与来源冲突时，后面的状态不得回跳到 Fact
Extraction、JD Parsing 或 Content Selection。`Targeted Repair` 只回到 validator，不得触发
全量 rewrite。

## Draft First, Validate Second

Draft 阶段只遵守已经确定的 Fact Store、scope 和 JD Evidence Map：

- 不逐条手工模拟 `validate_claims.py`；
- 不预测正则、scope detector、duplicate similarity threshold 或 ATS keyword detector；
- 不为猜测 issue code 是否触发而反复换词；
- 先生成完整候选 Resume + Claims，再实际运行 deterministic validator。

LLM 负责语义生成和事实边界内的自然表达；validator 是检测 Runtime 的真实反馈源。

## ERROR / WARNING

`ERROR` 是 blocking：必须定位受影响的 claim / bullet，局部修复后重新验证。两轮后仍存在
ERROR 时，停止自动修改，报告 `unresolved ERROR`，不得继续思考或假装通过。

`WARNING` 是 non-blocking heuristic signal，不是 reward function，也不是 Runtime Success
Criterion。`remaining_warnings > 0` 不会自动失败；绝对不能把 `0 WARNING` 当成优化目标。

只在以下情况下修复 warning：

- 可能造成事实误导或 scope 明显越界；
- 明显影响 JD 核心 Evidence 的理解；
- 明显重复并占用关键空间；
- 明显妨碍招聘者快速理解核心价值。

边缘风格、轻微 heuristic 相似、修复会削弱事实表达、两种表达都合理，或收益低于引入新问题
的风险时，可以保留 warning，并在交付说明中报告。`Resume Quality > Warning Count`。

## Targeted Repair

每轮按以下闭环执行，并只处理实际 validator 输出：

```text
validator issue
→ locate affected claim / bullet
→ local patch
→ validator
```

不允许：`repair → 全量 rewrite → 全局重新检查 → repair`。同一 issue code 连续两轮仍存在时，
停止继续改写，优先保留事实更安全、语义更自然的版本；如果是 ERROR，报告未解决阻断；
如果是 warning，允许保留。

ATS 与 Salience 的局部修改若改变了 claim 文本，必须在剩余 validator 预算内复验；预算已耗尽
时不再修改事实措辞，只报告风险。它们不能开启新的修复轮次或重新做全局优化。

## Runtime Metrics 与 PASS

正常运行记录以下指标；它们是执行约束，不是质量评分：

```yaml
draft_count: 1
validator_runs: 1..3
repair_rounds: 0..2
remaining_errors: 0..N
remaining_warnings: 0..N
runtime_pass: true | false
```

`runtime_pass = true` 的条件是 `remaining_errors == 0` 且 lint 没有 ERROR；WARNING 可以大于 0。
`draft_count != 1`、`validator_runs > 3` 或 `repair_rounds > 2` 都是 Runtime 协议违规。达到
修复预算后仍有 ERROR 必须 `runtime_pass: false` 并停止。

## 资源加载

基础 Runtime 只读取事实边界、JD 分析、scope、改写和输出格式等当前状态所需规则。

- 真实 scope issue → 读取 `scope-rules.md`；
- duplicate / 内容取舍 issue → 读取 `content-selection.md`；
- ATS issue → 读取 `ats-rules.md`；
- Salience issue → 读取 `recruiter-review.md`；
- 质量评测 / 发布验收 → 才读取 `evals/quality/`，且不进入正常生成链。

