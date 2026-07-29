#!/usr/bin/env python3
import re
import sys
from pathlib import Path


REQUIRED_SECTIONS = [
    "## 个人优势",
    "## 工作经历",
    "## 教育背景与认证",
]

ORDERED_SECTIONS = [
    "## 个人优势",
    "## 工作经历",
    "## 项目经历",
    "## 教育背景与认证",
]


def section_lines(lines, heading):
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return []
    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return lines[start:end]


def main():
    if len(sys.argv) != 2:
        print("用法: python3 lint_resume.py /path/to/resume.md")
        return 2

    path = Path(sys.argv[1]).expanduser().resolve()
    if not path.is_file():
        print(f"错误: 文件不存在: {path}")
        return 2

    raw_lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line.strip() for line in raw_lines]
    nonempty = [(index + 1, line) for index, line in enumerate(lines) if line]
    errors = []
    warnings = []

    if not nonempty:
        errors.append("简历不能为空")
    else:
        first_line = nonempty[0][1]
        has_name_heading = bool(re.fullmatch(r"# [^#].+", first_line))
        contact_index = 1 if has_name_heading else 0

        if has_name_heading and len(nonempty) < 2:
            errors.append("缺少联系方式行")
        elif len(nonempty) <= contact_index:
            errors.append("缺少联系方式行")
        else:
            contact = nonempty[contact_index][1]
            if not re.search(r"手机(?:/微信)?：", contact) or "邮箱：" not in contact:
                errors.append("联系方式行必须包含手机或手机/微信，以及邮箱")
            if "求职意向" in contact or "工作经验" in contact:
                errors.append("顶部联系方式行不得包含求职意向或工作经验")

    for section in REQUIRED_SECTIONS:
        if section not in lines:
            errors.append(f"缺少章节：{section}")

    positions = [lines.index(section) for section in ORDERED_SECTIONS if section in lines]
    if positions != sorted(positions):
        errors.append("章节顺序应为：个人优势、工作经历、项目经历、教育背景与认证")

    personal = [line for line in section_lines(lines, "## 个人优势") if line.startswith("- ")]
    if personal and not 3 <= len(personal) <= 4:
        warnings.append(f"个人优势建议 3–4 条，当前为 {len(personal)} 条")
    for line in personal:
        if not re.match(r"- \*\*[^*]{2,20}\*\*：", line):
            errors.append(f"个人优势未使用“**关键字**：内容”格式：{line}")

    work = section_lines(lines, "## 工作经历")
    for line in work:
        if line.startswith("- ") and not re.match(r"- \*\*[^*]{2,20}\*\*：", line):
            errors.append(f"工作经历未使用“**关键字**：内容”格式：{line}")

    projects = section_lines(lines, "## 项目经历")
    for line in projects:
        if line.startswith("- ") and not re.match(r"- \*\*[^*]{2,20}\*\*：", line):
            warnings.append(f"项目经历建议使用“**关键字**：内容”格式：{line}")

    full_text = "\n".join(lines)
    if "TODO" in full_text or "<!--" in full_text:
        errors.append("最终简历中不得保留 TODO 或 HTML 注释")
    if re.search(r"^- 【[^】]+】", full_text, flags=re.MULTILINE):
        errors.append("发现旧的【标签】格式，应改为“**关键字**：内容”")

    bullets = [re.sub(r"\s+", " ", line) for line in lines if line.startswith("- ")]
    duplicates = sorted({line for line in bullets if bullets.count(line) > 1})
    for line in duplicates:
        warnings.append(f"发现重复 bullet：{line}")

    vague_patterns = ["能力强", "经验丰富", "优秀的", "显著提升", "大幅提升", "全面负责"]
    for phrase in vague_patterns:
        if phrase in full_text:
            warnings.append(f"发现可能空泛的表达：{phrase}")

    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARN: {message}")

    if errors:
        print(f"检查失败：{len(errors)} 个错误，{len(warnings)} 个警告")
        return 1

    print(f"检查通过：0 个错误，{len(warnings)} 个警告")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
