# -*- coding: utf-8 -*-
"""
本地仪表盘服务：每 60 秒重新生成 dashboard.html，并托管在
http://127.0.0.1:8787/dashboard.html

用法：
    python serve.py
"""

import argparse
import os
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import report
from paths import BASE_DIR, OUTPUT_DIR, DATA_DIR, PID_SERVE as PID_FILE, REPORT_PATH


def regen_report():
    report.generate_report()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass


def acquire_pid():
    os.makedirs(DATA_DIR, exist_ok=True)
    if _is_already_running():
        print("仪表盘服务已在运行，本实例退出（单实例限制）。")
        sys.exit(0)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


_MUTEX_HANDLE = None


def _is_already_running():
    """单实例检查：优先用 Windows 命名互斥锁，失败时退回 PID 文件检查。"""
    global _MUTEX_HANDLE
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\DiskIOMonitorDashboard")
            if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                return True
        except Exception:
            pass
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, encoding="utf-8") as f:
                old = int(f.read().strip())
            if _pid_alive(old) and _pid_is_dashboard(old):
                return True
        except (ValueError, OSError):
            pass
    return False


def _pid_is_dashboard(pid):
    """确认该 PID 确实属于本程序（避免 PID 被系统复用后误判）。"""
    try:
        name = psutil.Process(pid).name().lower()
        return name in ("serve.exe", "python.exe", "pythonw.exe")
    except Exception:
        return False


def release_pid():
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            pid = f.read().strip()
        # 先关闭文件再删除，避免 Windows 下文件被占用
        if pid == str(os.getpid()):
            os.remove(PID_FILE)
    except OSError:
        pass


def _pid_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return True


def main():
    ap = argparse.ArgumentParser(description="本地磁盘监控仪表盘服务")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--interval", type=int, default=60, help="报告刷新间隔（秒）")
    args = ap.parse_args()

    acquire_pid()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    regen_report()

    def refresh_loop():
        while True:
            time.sleep(args.interval)
            regen_report()

    threading.Thread(target=refresh_loop, daemon=True).start()

    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("仪表盘已启动：http://127.0.0.1:%d/dashboard.html" % args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        release_pid()


if __name__ == "__main__":
    main()
