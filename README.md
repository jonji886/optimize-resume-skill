# optimize-resume

面向中文技术与企业服务岗位的简历优化 Skill。根据岗位 JD 诊断匹配度、补齐事实证据、筛选经历并生成或迭代更新目标简历。

## 能力

- 拆解 JD 的必备项、加分项、职级和行业要求；
- 建立“已确认 / 已否认 / 未知 / 可迁移”事实账本，避免多轮改写带回错误信息；
- 按直接匹配、可迁移和无关内容筛选经历；
- 校准主导、核心执行和支持等参与层级；
- 处理个人优势、工作经历、项目经历、核心技能和教育认证的结构；
- 支持首次诊断、直接改写、现有简历迭代和仅分析四种模式；
- 提供 Markdown 简历格式检查脚本。

## 使用

提供目标岗位 JD 与原始简历或现有目标简历，然后调用：

~~~
$optimize-resume
~~~

首次优化且存在关键事实缺口时，Skill 会先提出少量针对性问题；信息完整或修改现有简历时直接更新文件。

默认输出：

~~~
姓名-{岗位名称}.md
~~~

简历顶部只保留姓名、手机/微信和邮箱。

## 格式检查

~~~
python3 scripts/lint_resume.py /absolute/path/to/resume.md
~~~

脚本检查章节、顶部信息、关键字格式、重复 bullet 和残留注释。真实性仍需依据用户确认的信息核对。

## 文件结构

~~~
optimize-resume/
├── SKILL.md
├── agents/openai.yaml
├── references/
│   ├── examples.md
│   └── role-mappings.md
└── scripts/lint_resume.py
~~~
