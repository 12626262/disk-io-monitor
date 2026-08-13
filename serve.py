# -*- coding: utf-8 -*-
"""
本地仪表盘服务：每 60 秒重新生成 dashboard.html，并托管在
http://127.0.0.1:8787/dashboard.html

用法：
    python serve.py
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import psutil

import report
from paths import BASE_DIR, OUTPUT_DIR, DATA_DIR, PID_SERVE as PID_FILE, REPORT_PATH, acquire_mutex


def regen_report():
    report.generate_report()


def send_json(handler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


FILEWATCH_EXE = os.path.join(BASE_DIR, "filewatch.exe")
FILEWATCH_PY = os.path.join(BASE_DIR, "filewatch.py")
FILEWATCH_PID = os.path.join(DATA_DIR, "filewatch.pid")


def filewatch_running():
    try:
        if os.path.exists(FILEWATCH_PID):
            with open(FILEWATCH_PID, encoding="utf-8") as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                try:
                    name = psutil.Process(pid).name().lower()
                except Exception:
                    name = ""
                if name in ("filewatch.exe", "python.exe", "pythonw.exe"):
                    return True
    except Exception:
        pass
    return False


def start_filewatch(admin=False):
    if filewatch_running():
        return {"ok": True, "running": True}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        stop_file = os.path.join(DATA_DIR, "live.stop")
        if os.path.exists(stop_file):
            os.remove(stop_file)
    except OSError:
        pass

    if os.path.exists(FILEWATCH_EXE):
        cmd = FILEWATCH_EXE
        args = ""
    else:
        cmd = sys.executable
        args = '"%s"' % FILEWATCH_PY

    if admin:
        import ctypes
        res = ctypes.windll.shell32.ShellExecuteW(None, "runas", cmd, args, BASE_DIR, 1)
        if res <= 32:
            return {"error": "启动管理员模式失败（错误码 %d），可能已被取消。" % res}
        return {"ok": True, "pending": True}

    try:
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        if args:
            subprocess.Popen([cmd, FILEWATCH_PY], cwd=BASE_DIR, creationflags=creationflags)
        else:
            subprocess.Popen([cmd], cwd=BASE_DIR, creationflags=creationflags)
    except Exception as exc:
        return {"error": "启动失败：%s" % exc}

    time.sleep(2.5)
    try:
        with open(os.path.join(DATA_DIR, "live.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if data.get("error"):
        return {"error": data["error"], "needAdmin": True}
    return {"ok": True, "running": True}


def stop_filewatch():
    try:
        with open(FILEWATCH_PID, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except Exception:
        pid = None
    try:
        with open(os.path.join(DATA_DIR, "live.stop"), "w", encoding="utf-8") as f:
            f.write("1")
    except OSError:
        pass
    if pid:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass
    return {"ok": True}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=OUTPUT_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass

    def _handle_api(self):
        if self.path == "/api/live/data":
            try:
                with open(os.path.join(DATA_DIR, "live.json"), "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {"running": False, "rows": []}
            send_json(self, data)
            return True
        if self.path == "/api/live/status":
            send_json(self, {"running": filewatch_running()})
            return True
        if self.path == "/api/flush":
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(os.path.join(DATA_DIR, "flush.request"), "w", encoding="utf-8") as f:
                    f.write("1")
                send_json(self, {"ok": True})
            except Exception as exc:
                send_json(self, {"ok": False, "error": str(exc)}, 500)
            return True
        if self.path == "/api/live/start" or self.path.startswith("/api/live/start?"):
            admin = "admin=1" in self.path
            send_json(self, start_filewatch(admin))
            return True
        if self.path == "/api/live/stop":
            send_json(self, stop_filewatch())
            return True
        return False

    def do_GET(self):
        if self._handle_api():
            return
        super().do_GET()

    def do_POST(self):
        if self._handle_api():
            return
        send_json(self, {"error": "not found"}, 404)


def acquire_pid():
    os.makedirs(DATA_DIR, exist_ok=True)
    if _is_already_running():
        print("仪表盘服务已在运行，本实例退出（单实例限制）。")
        sys.exit(0)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def _is_already_running():
    # Single-instance check: named mutex first, PID file as fallback.
    if os.name == "nt":
        got = acquire_mutex("Local\\DiskIOMonitorDashboard")
        if got is not None:
            return not got
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
