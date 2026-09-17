"""optimize-resume Eval 体系。

    evals/regression/   有没有做错（守住下限，deterministic pass/fail）
    evals/quality/      有没有做得更好（判断上限，Blind Pairwise + Position Swap）
    evals/common/       两层共用的加载 / 结构 / judge / 报告
    evals/tests/        Harness 自身的测试
    evals/reports/      评测报告（JSON + Markdown）
"""
