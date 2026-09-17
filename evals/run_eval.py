#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向后兼容入口：把旧的 `evals/run_eval.py` 转发到 Regression Eval runner。

Regression Eval 已迁移到 `evals/regression/run_regression.py`，本文件只做转发，
保证既有文档 / 脚本 / CI 中 `python3 evals/run_eval.py ...` 的命令继续可用。

注意：`--suite cases` 现在读取 `evals/regression/cases/`。版本优劣判断不在这里，
见 `evals/quality/run_quality.py`。
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from regression.run_regression import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
