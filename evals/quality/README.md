# Quality / Capability Benchmark

**Quality Eval 只回答一个问题：两个版本都没有明显事实错误时，哪个 Skill 版本生成的简历更有效？**

它判断的是**上限**，而 `evals/regression/` 守的是**下限**。两者不能互相替代。

```text
Regression Eval   有没有做错         deterministic，pass/fail        目标 100% 通过
Quality Eval      有没有做得更好     Blind Pairwise + Position Swap  目标 wins > losses
```

Quality Eval 属于 **development / release 评测**，默认不进入 Resume Skill 的正常执行链。

---

## 1. 为什么不用「匹配度 0～100」

```text
Resume Match Score = 87
```

这个数字看起来直观，但它不可比较、不可解释、不可复现：

| 问题 | 说明 |
|---|---|
| 不同 JD 难度不同 | 一个 87 分的简单岗位匹配，和一个 87 分的跨行转型，含义完全不同 |
| Judge 绝对分会漂移 | 同一份简历换一次调用可能 84，也可能 89 |
| 84 与 87 的差异无法解释 | 无法说明「为什么少了 3 分」，也无法据此改 Skill |
| 模型会固化自己的尺度 | 一旦出现「上次给 87」，模型会向历史分数锚定 |
| 跨 case 不可相加 | 把 20 个 case 的绝对分平均，得到的是一个没有意义的数 |

所以绝对分最多只能作为 **diagnostic signal**，不能作为版本升级的核心依据。

## 2. 核心实验单位：Blind Pairwise

正确比较方式必须是**同一条件下换版本**：

```text
Same Facts
Same Source Resume
Same JD
Same Relevant Context
        │
    ┌───┴────┐
    ↓        ↓
 Skill V1   Skill V2
    ↓        ↓
 Resume A   Resume B
    └───┬────┘
        ↓
 Independent Blind Judge
        ↓
     A / Tie / B
```

不同 JD 之间不比较绝对分；同一个 case 内的两份产物才做比较。

## 3. 为什么必须 A/B swap

只跑一个顺序，无法区分「B 真的更好」和「Judge 偏爱位置 B」。所以每个 case 至少两轮：

```text
Run 1   A = baseline , B = candidate
Run 2   A = candidate, B = baseline
```

再映射回真实版本：

```text
Run1 winner = B (candidate)  +  Run2 winner = A (candidate)   →  CONSISTENT candidate win
Run1 winner = A (baseline)   +  Run2 winner = A (candidate)   →  POSITION_INCONSISTENT
Run1 winner = A              +  Run2 winner = Tie             →  POSITION_INCONSISTENT
```

- `POSITION_INCONSISTENT` **不计入** Win Rate 的分母，但原始结果完整保留在报告里；
- 两轮都判 `Tie` 才记为平局（一致）；
- 第一轮谁在 A 位由 `--seed` 决定（默认 `0`，写入报告以保证可复现）。

## 4. 干净上下文（Judge 隔离约束）

Judge 与被测系统隔离，**不允许**知道：

```text
哪份是新版 / 哪份是旧版
开发者希望哪个版本获胜
Generator 的 reasoning
之前的对话历史
任何版本号、Skill 名称、文件路径
```

Judge 输入只有：`Target JD`、`Source Facts`、`Source Resume`、`Candidate A`、`Candidate B`、`Rubric`。

实现上由 `render.py` 统一渲染，且只传简历正文——不传 claims 侧车、不传 fact gate 结果、
不传 evidence recall，避免把「我们认为哪里好」泄漏给 Judge。

## 5. Fact Safety 是 Hard Gate，不是评分维度

出现以下任一情况，该版本在本 case 中**直接判负**，不允许用「表达很好」抵消：

```text
Unsupported Claim
Fabricated Metric
Scope Inflation
Project Boundary Violation
Denied Fact Reintroduced
Transferable 写成 Direct
输出残留占位符
```

判定结果：

| 情况 | 处理 |
|---|---|
| candidate FAIL，baseline PASS | `CANDIDATE_FACT_FAIL` → candidate 输 |
| baseline FAIL，candidate PASS | `BASELINE_FACT_FAIL` → candidate 由 Gate 直接获胜（`decided_by=fact_gate`） |
| 双方都 FAIL | `BOTH_FACT_FAIL` → case 视为 INVALID，不做质量比较 |

Fact Gate 分两层：

1. **deterministic（默认）**：复用 `scripts/validate_claims.py`，只把属于事实安全类别的
   issue code 作为否决项。可代码判断的事情不用 LLM 判断。被声明进
   `GATE_CATEGORY_BY_CODE` 的 code 一律升级为否决项（例如
   `RESUME_BULLET_UNVERIFIED` 默认只是 warning，但「带数字的 bullet 没有 claim 记录」
   就是没有 evidence 的数字，必须否决）。
2. **semantic（`--semantic-fact-guard`，可选）**：用 `judges/fact_guard.md` 补
   deterministic 抓不到的同义改写、语义级 scope 夸大、项目边界越界。
   它**只能增加**否决项，不能把 deterministic 的失败改判为通过。

`--strict-gate` 会额外把「需要先确认」的 warning（`SCOPE_OVERREACH`、
`SCOPE_UNVERIFIABLE`、`PROJECT_COMMERCIAL_UNVERIFIED`）升级为否决项。默认关闭，
因为这些是待确认信号，不是已确认的违规。

`forbidden_claims` 只作为**诊断信号**（`gate.forbidden_hits`）在报告里列出，不参与否决：
中文表述天然可以同义改写，token 覆盖率匹配必然有误报，最终判定交给
fact guard / 人工。

## 6. JD Evidence Recall

核心指标，定义是：

```text
最终简历成功表达出的高价值真实 Evidence
──────────────────────────────────────────
Fact Store 中实际可用于该 JD 的高价值 Evidence
```

**关键约束：分母只包含候选人真实拥有的 evidence。**

> 如果 Fact Store 里根本没有 Kubernetes 经验，那么简历没写 Kubernetes 不扣分。
> 这里测的是「现有真实证据有没有被最大化利用」，不是「有没有凭空变得和 JD 一样」。

实现（`evidence.py`，deterministic）：

- 可用集合 = benchmark `important_capabilities[].evidence_facts` 中
  status ∈ `{confirmed, transferable}` 的 fact（`denied` / `unknown` 不计入分母）；
- 权重 = capability 的 `weight`（1–3），同一个 fact 取最高权重；
- 「已表达」= 该 fact 被某个 claim 引用，且该 claim 的文本确实出现在简历里；
  没有 claims 侧车时退化为 fact 陈述与简历 bullet 的相似度近似（报告标注 degraded）；
- `must_preserve` 是 must 子集，命中 / 缺失单独统计。

## 7. Judge 角色拆分

MVP 只要求跑通，所以实际只有 **1 个必需 Judge + 1 个可选语义 Judge**：

| Judge | 文件 | 输入 | 开关 | 说明 |
|---|---|---|---|---|
| Judge 3 Pairwise Judge | `judges/pairwise_judge.md` | JD + Fact Store + Source Resume + A + B | **默认必需** | 主判定：盲评哪一份更有效 |
| Judge 1 Fact Guard | `judges/fact_guard.md` | Fact Store + Source Resume + 一份 Candidate | `--semantic-fact-guard` | 只输出事实安全发现；**只能增加**否决项 |
| Judge 2 JD Evidence Judge | `judges/evidence_judge.md` | JD + Fact Store + 一份 Candidate | `--evidence-judge` | 诊断：逐条需求判断 evidence 是否存在 / 是否可见；输出 `evidence_recall`，只写报告，不参与胜负判定 |

三个职责共用同一个模型接口（`JudgeClient`）；是否真的拆成多个 Agent 由实现成本决定，
本实现里只有 Pairwise 是必需的。Judge 1 / Judge 2 都只能在**真实 judge 模式**下启用：
mock judge 没有语义判断能力，让它冒充语义检查会制造虚假的安全感，
所以 `--mock` 时这两个开关会被忽略并给出提示。

Judge 2 的 deterministic 等价物是 `evidence.py`，无需 LLM、默认一直运行。

### Pairwise 的 7 个维度

```text
jd_evidence_coverage      JD Evidence Coverage
evidence_strength         Evidence Strength
recruiter_salience        Recruiter Salience
information_density       Information Density
redundancy_conciseness    Redundancy / Conciseness
ats_terminology           ATS Terminology Coverage
interview_defensibility   Interview Defensibility
                          + overall(winner, confidence, reason)
```

每个维度只输出 `A / B / Tie` + 一句话理由，**不给绝对分**；`overall` 单独给 confidence。

Prompt 里明确写死的反作弊原则：

```text
Do not reward unsupported claims.
Do not reward Candidate A or B merely because it contains more JD keywords.
Do not reward a stronger tone（参与 → 主导、支持 → Owner、了解 → 精通 不算改进）.
Prefer strong, specific, defensible evidence over generic claims.
Prefer concise, high-information-density resumes. Penalize redundancy.
Do not infer skills that are not written. Do not assume missing evidence exists.
Do not judge visual design.
Fact safety 不在这里打分，但也不得奖励不安全的表述。
```

## 8. Golden Benchmark

```text
evals/quality/benchmark/
├── benchmark.yaml                     # 版本 / 覆盖 / 新增门槛
├── case.schema.json                   # case 结构校验
├── shared/persona-*/{resume.md,facts.yaml}   # 可被多个 case 复用的候选人事实
└── <role-family>/<case-id>/
    ├── case.yaml
    └── jd.md
```

当前 **12 个 case**，覆盖 8 个 role family：`ai-fde`、`ai-delivery`、`ai-solutions`、
`technical-consultant`、`technical-support`、`agent-product`、`presales`、`customer-success`。
来源比例：`real-world` 3 + `real-world-derived` 6 + `synthetic` 3。

为什么不是 100 个 case：10–20 个高价值 case 比 100 个低质量 case 更有价值。
优先覆盖典型岗位、高频错误、极端边界、跨度较大的岗位。目标 20–30 个，
新增 case 的成本必须接近于零。

### case 格式

```yaml
id: ai-fde-001

metadata:
  role_family: ai-fde
  difficulty: medium              # easy / medium / hard
  source: real-world-derived      # real-world / real-world-derived / synthetic
  notes: "这个 case 在防什么"

inputs:                           # 路径相对 case 目录，找不到则回退到 benchmark 根目录
  source_resume: shared/persona-a/resume.md
  fact_store: shared/persona-a/facts.yaml
  jd: jd.md
  # context: notes.md            # 可选：额外相关上下文

expectations:
  must_preserve: [fact-a01, fact-a02]        # 必须在最终简历中可见的 fact id
  forbidden_claims: ["支付行业商业项目经验"]   # 语义级禁写项，不是逐字匹配
  important_capabilities:
    - capability: customer_delivery
      label: 企业客户交付与上线验收
      surfaces: [客户交付, 上线验收, 联调]
      evidence_facts: [fact-a01, fact-a02]
      weight: 3

human_review:                     # 可选：人工盲评标签，用于 Judge Calibration
  winner: B                       # A = baseline 产物，B = candidate 产物，Tie = 平局
  reviewer: human
  notes: "..."
```

**期望值是语义与 evidence，不是固定文案。** 评测不要求生成完全一样的文字。

### 新增一个 case

1. 新建目录 `benchmark/<role-family>/<case-id>/`；
2. 放入 `case.yaml` 与 `jd.md`；事实复用 `shared/persona-*/`，或新增一份 persona；
3. 运行 `python3 evals/quality/run_quality.py --dry-run --case <case-id>` 校验加载与路径；
4. 运行 `python3 -m unittest discover -s evals/tests -t .` 确认 schema 与 mock 一致性通过。

不需要改任何代码。

### Human Calibration

Judge 不是 Ground Truth。建议按 **10%–20%** 抽样填写 `human_review.winner`，
运行后报告会给出 `LLM Judge vs Human Agreement`。如果一致率长期偏低，
应该调整的是 `rubric` / `judge prompt` / `judge model`，而不是直接相信 Judge。

## 9. 运行方式

### 不需要 API Key（离线自检）

```bash
# 只验证 benchmark 加载、路径解析、Judge Input、A/B 随机化
python3 evals/quality/run_quality.py --dry-run

# 跑完整流水线：确定性 mock 产物 + 确定性 mock judge
python3 evals/quality/run_quality.py --mock --baseline v0.6 --candidate v0.7

# 已知答案自检：Harness 必须分别判出三个方向
python3 evals/quality/run_quality.py --mock --mock-mode candidate-wins
python3 evals/quality/run_quality.py --mock --mock-mode equal
python3 evals/quality/run_quality.py --mock --mock-mode baseline-wins
```

`--mock` 的报告文件名会带 `-mock-<mode>` 后缀，并在正文顶部标注「不是真实质量判断」，
避免被当成有效结论。

### 真实 Judge

```bash
# 单 case
python3 evals/quality/run_quality.py --case ai-fde-001 --dry-run

# 版本对比（先准备 evals/quality/runs/<case-id>/{baseline,candidate}.md[+.facts.yaml]）
python3 evals/quality/run_quality.py \
  --baseline v0.6 --candidate v0.7 \
  --runs-dir evals/quality/runs

# 接入真实模型（OpenAI 兼容接口）
QUALITY_JUDGE_PROVIDER=openai QUALITY_JUDGE_MODEL=gpt-4o OPENAI_API_KEY=... \
  python3 evals/quality/run_quality.py --baseline v0.6 --candidate v0.7

# 接入 Anthropic
QUALITY_JUDGE_PROVIDER=anthropic QUALITY_JUDGE_MODEL=claude-3-5-sonnet-latest \
ANTHROPIC_API_KEY=... python3 evals/quality/run_quality.py ...

# 接入自研判定器（stdin 收 JSON，stdout 返回 JSON）
python3 evals/quality/run_quality.py --judge-provider subprocess \
  --judge-cmd "python3 my_judge.py" --baseline v0.6 --candidate v0.7

# 额外启用语义层事实安全（Judge 1）
python3 evals/quality/run_quality.py --semantic-fact-guard ...

# 额外启用 LLM 版 JD Evidence 诊断（Judge 2）
python3 evals/quality/run_quality.py --evidence-judge ...

# 严格 Gate：把 scope / 商业交付的「待确认」warning 也升级为否决项
python3 evals/quality/run_quality.py --strict-gate ...
```

### 环境变量

| 变量 | 含义 |
|---|---|
| `QUALITY_JUDGE_PROVIDER` | `mock` / `subprocess` / `openai` / `anthropic` |
| `QUALITY_JUDGE_MODEL` | 模型名 |
| `QUALITY_JUDGE_CMD` | subprocess judge 命令（兼容旧的 `EVAL_JUDGE_CMD`） |
| `QUALITY_JUDGE_ENDPOINT` | OpenAI 兼容网关地址 |
| `QUALITY_JUDGE_API_KEY_ENV` | 存放 API Key 的环境变量名 |
| `QUALITY_JUDGE_TEMPERATURE` | 采样温度，默认 `0` |

Judge 接口见 `common/judge_client.py`：

```python
class JudgeClient:
    def complete(self, system: str, user: str,
                 payload: dict | None = None) -> str: ...
```

核心 Eval 逻辑只依赖这个接口，不写死任何模型供应商。

### 主要参数

| 参数 | 说明 |
|---|---|
| `--case` | 只跑指定 case，可逗号分隔或重复传入 |
| `--role-family` | 只跑某一类岗位 |
| `--runs-dir` | 两套产物所在目录 |
| `--dry-run` | 不调用 Judge |
| `--mock` / `--mock-mode` | 离线 mock 与已知答案自检 |
| `--semantic-fact-guard` | 启用 Judge 1（LLM 语义层事实安全检查） |
| `--evidence-judge` | 启用 Judge 2（LLM JD Evidence 诊断） |
| `--strict-gate` | 把待确认的 warning 也升级为否决项 |
| `--seed` | A/B 位置随机种子（写入报告） |
| `--fail-on-regression` | 存在退化 case 时返回非零退出码 |
| `--json` | 输出机器可读结果 |

## 10. 版本比较指标

报告至少输出：

```text
Total Cases              Candidate Wins         Baseline Wins
Ties                     Inconsistent Cases     Both Fact Fail
Candidate Fact Fail      Baseline Fact Fail     Judge Execution Failure
Valid Comparable Cases

Candidate Win Rate       = Candidate Wins / Valid Comparable Cases
Candidate Loss Rate      = Baseline Wins  / Valid Comparable Cases
Tie Rate                 = Ties           / Valid Comparable Cases
Regression Rate          = Baseline Wins  / Valid Comparable Cases
Position Inconsistency Rate = Inconsistent / Judged Cases
```

`Valid Comparable Cases` 排除：

```text
Position inconsistent
Invalid benchmark（Both Fact Fail）
Judge execution failure
```

分子分母都显式写在报告里；**原始总数与失败样本一律保留，不偷偷删掉**。

### Drill-down

```text
Overall
V2 Win Rate: 61%

By Role Family:
  ai-fde            7 / 10 wins
  ai-solutions      4 / 7  wins
  technical-support 1 / 6  wins        ← 退化集中在这里，优先排查
```

样本量小于 5 时报告会显式标注 `sample size too small`，不用小样本下强结论。

### Regression Rate

> 新版 Skill 在旧版表现更好的 case 中退化的比例。

报告会直接列出 `regression_cases` 的 case id，便于回答
「v0.7 为什么在 technical-support-003 上退化？」。

### 报告文件

```text
evals/reports/2026-09-17-v0.6-vs-v0.7.json    完整结果
evals/reports/2026-09-17-v0.6-vs-v0.7.md      人读摘要
```

包含：baseline / candidate version、judge model、benchmark version、run timestamp、
每个 case 的结果与 Judge 理由、聚合指标、regression cases、inconsistent cases、
fact violations、human agreement。

**可复现性记录**：Skill version、benchmark version、judge provider/model/temperature/
max_tokens/endpoint/key 是否存在、pairwise prompt 版本、fact guard prompt 版本、
fact gate 模式、position swap 说明、random seed，全部写进报告元信息。
缺这些数字以后就不可比较。

## 11. Release Gate（建议值，不是硬阈值）

先积累 benchmark 再谈阈值。当前建议：

```text
Fact Safety            candidate 不得相对 baseline 退化（candidate_fact_fail == 0）
Regression Eval        critical cases 100% pass
Quality                candidate win rate > loss rate
Critical regression    0 个 regression case
```

不要一开始就制定 `Win Rate > 80%` 这类没有数据基础的阈值。

## 12. 明确不做的事

| 不做 | 原因 |
|---|---|
| 用一个 Judge 给总分解决全部问题 | 绝对分不可比较、不可解释，见第 1 节 |
| 用 LLM 替代 deterministic 检查 | 可以代码判断的事情用代码判断，更便宜也更稳 |
| 把 Generator reasoning 传给 Judge | Judge 必须干净上下文，见第 4 节 |
| 告诉 Judge 哪个是新版 | 必须 blind |
| 只跑一个 A/B 顺序 | 无法区分真实优势与位置偏差 |
| 因为写得长就认为更好 | 信息密度 > 信息数量 |
| 无限制 `Generate → Judge → Refine` 循环 | 成本高、易 reward hacking、可能越改越差 |
| 为了让数字好看而剔除失败 case | Win Rate 的分母口径必须公开 |
| 为 Eval 引入大型框架 | 现有 Python 脚本足够，不引入第三方 Eval 平台 |

如果以后要加 Refine：**最多 1 次**，且只针对明确的 Judge Feedback。本次不作为 P0。

## 13. 当前缺口

| 缺口 | 说明 |
|---|---|
| 真实 Judge API 未配置 | 目前所有端到端验证使用 `--mock` / subprocess stub；真实模型结论需要自行配置后再跑 |
| Benchmark 规模 | 12 个 case，目标 20–30；role family 内样本普遍 < 5，drill-down 只能看方向 |
| 尚无人工盲评标签 | `human_review` 通路已实现并有单测覆盖，但 benchmark 里还没有真实人工标签 |
| 无自动 Git 版本 runner | 需要人工把两个版本的产物放进 `runs/<case-id>/`（这是刻意的简化） |
| Judge 1 / Judge 2 从未真实运行过 | 两条通路已实现并有离线单测覆盖，但尚未用真实模型跑过，输出质量未验证 |
| `--strict-gate` 未在真实产物上调过阈值 | 目前只在 mock fixture 上验证过不会误伤 |
