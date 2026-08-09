# -*- coding: utf-8 -*-
"""
统一的路径解析：
- 源码运行时，所有文件都放在脚本目录下；
- 打包成 exe 后，所有文件都放在 exe 所在目录下，方便整包拷贝到别的电脑。
"""

import os
import sys

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "disk_io.db")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
REPORT_PATH = os.path.join(OUTPUT_DIR, "dashboard.html")

PID_COLLECTOR = os.path.join(DATA_DIR, "collector.pid")
PID_SERVE = os.path.join(DATA_DIR, "serve.pid")
LOG_COLLECTOR = os.path.join(DATA_DIR, "collector.log")