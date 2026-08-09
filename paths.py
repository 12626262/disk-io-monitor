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


_MUTEX_KERNEL32 = None
_MUTEX_HANDLES = {}


def acquire_mutex(name):
    # Create a Windows named mutex.
    # Returns:
    #   True  -- this process created it (safe to continue)
    #   False -- another instance already holds it
    #   None  -- mutex unavailable (caller should fall back)
    global _MUTEX_KERNEL32
    try:
        import ctypes
        if _MUTEX_KERNEL32 is None:
            _MUTEX_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
            _MUTEX_KERNEL32.CreateMutexW.restype = ctypes.c_void_p
            _MUTEX_KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        handle = _MUTEX_KERNEL32.CreateMutexW(None, False, name)
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _MUTEX_HANDLES[name] = handle
        return True
    except Exception:
        return None
