"""optimize-resume Eval 公共层。

只放 regression 与 quality 两个 runner 都会用到的东西：

    loaders       YAML / Markdown / prompt frontmatter / benchmark 与 run 产物加载
    schemas       benchmark case 与 judge 输出的结构校验
    judge_client  Judge 模型接口（mock / subprocess / openai / anthropic）
    report        结果聚合与 JSON + Markdown 报告输出

不在这里实现任何具体评测逻辑：具体逻辑属于 regression/ 或 quality/。
"""
