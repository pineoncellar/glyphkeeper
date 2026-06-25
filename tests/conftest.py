# -*- coding: utf-8 -*-
"""
@File     :   conftest.py
@Desc     :   确保 tests/ 下的测试可以导入 src/ 包
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
