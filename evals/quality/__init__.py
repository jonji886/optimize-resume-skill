"""Quality / Capability Benchmark：判断「新版本 Skill 是否生成了更好的简历」。

与 `evals/regression/` 的职责边界：

    regression/   有没有做错       deterministic，pass/fail，守住下限
    quality/      有没有做得更好   Blind Pairwise + Position Swap，判断上限

Quality Eval 属于 development / release 评测，不进入 Resume Skill 的正常执行链路。
"""
