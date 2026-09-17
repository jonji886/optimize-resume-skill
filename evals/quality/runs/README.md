# runs/ — 版本对比产物目录

Quality Eval 比较的是**同一份事实、同一份 JD 下两个 Skill 版本各自的产出**。
这个目录存放这些产出，运行结果不进版本库（见根目录 `.gitignore`）。

## 目录约定

```text
runs/
└── <case-id>/
    ├── baseline.md            # baseline 版本生成的简历
    ├── baseline.facts.yaml    # 该版本的 Fact Store（含 claims）
    ├── candidate.md
    └── candidate.facts.yaml
```

- `<case-id>` 必须与 `benchmark/**/case.yaml` 里的 `id` 一致；
- `.facts.yaml` 缺失不会报错，但会让 **Fact Gate 退化**为简历级检查、
  **Evidence Recall 退化**为文本相似度近似，报告里会明确标注；
- `baseline` / `candidate` 只是角色名：谁是被测新版本由 `--candidate` 参数决定。

## 生产产物的方式

现实做法是让两个 Skill 版本各自跑一遍同一个 case，把输出按上面的名字存进来。
本仓库不提供自动 Git 版本 runner（那会引入不必要的复杂度）：

```bash
# 例如在 v0.6 的工作区里跑完整流程，产物拷进 runs/<case-id>/baseline.*
# 再切到 v0.7，产物拷进 runs/<case-id>/candidate.*
python3 evals/quality/run_quality.py --runs-dir evals/quality/runs \
  --baseline v0.6 --candidate v0.7
```

`--mock` 也会往这里写一对 mock 产物，方便离线查看差异、复现 Judge 输入：

```bash
python3 evals/quality/run_quality.py --mock
```
