# -*- coding: utf-8 -*-
"""
filewatch.py - 实时文件级读写监控（需要管理员权限）

通过 ETW 跟踪 Microsoft-Windows-Kernel-File，按（进程, 文件）聚合读写字节数，
每 1 秒写入 data/live.json，供网页实时展示。

用法：
    python filewatch.py            # 前台运行（需要管理员权限）
"""

import ctypes
import json
import os
import sys
import threading
import time
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from paths import DATA_DIR

PID_FILE = os.path.join(DATA_DIR, "filewatch.pid")
LIVE_JSON = os.path.join(DATA_DIR, "live.json")
STOP_FILE = os.path.join(DATA_DIR, "live.stop")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_device_map = {}


def build_device_map():
    """把 \\Device\\HarddiskVolumeN 映射成盘符（C:\\ 等）。"""
    try:
        buf = ctypes.create_unicode_buffer(512)
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = letter + ":"
            if ctypes.windll.kernel32.QueryDosDeviceW(drive, buf, 512):
                _device_map[buf.value.lower()] = drive
    except Exception:
        pass


def normalize_path(path):
    if not path:
        return path
    low = path.lower()
    for dev, drive in _device_map.items():
        if low.startswith(dev):
            return drive + path[len(dev):]
    return path


def to_int(value):
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip(), 0)
    except Exception:
        return None


def to_str(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "replace")
        except Exception:
            return ""
    if isinstance(value, dict):
        for val in value.values():
            s = to_str(val)
            if s:
                return s
        return ""
    return str(value)


filekey_path = {}
stats_lock = threading.Lock()
stats = defaultdict(lambda: [0, 0])  # (pid, path) -> [读, 写]


def on_event(event_tup):
    """ETW 事件回调：event_tup = (event_id/opcode, 解析后的字段字典)"""
    try:
        event_id, ev = event_tup
        pid = ev["EventHeader"]["ProcessId"]
        if event_id in (12, 13):  # FileIo/Name, FileIo/FileCreate：记录 FileKey -> 路径
            fkey = to_int(ev.get("FileKey"))
            fname = normalize_path(to_str(ev.get("FileName")))
            if fkey is not None and fname:
                with stats_lock:
                    if len(filekey_path) > 60000:
                        filekey_path.clear()
                    filekey_path[fkey] = fname
        elif event_id in (4, 5):  # FileIo/Read, FileIo/Write
            fkey = to_int(ev.get("FileKey"))
            size = to_int(ev.get("SizeOfIo"))
            if fkey is None or size is None:
                return
            with stats_lock:
                path = filekey_path.get(fkey)
            if not path:
                return
            key = (pid, path)
            with stats_lock:
                stats[key][0 if event_id == 4 else 1] += size
    except Exception:
        pass


proc_name_cache = {}
proc_name_ts = {}


def process_name(pid):
    now = time.time()
    if pid in proc_name_cache and now - proc_name_ts.get(pid, 0) < 10:
        return proc_name_cache[pid]
    name = "PID %d" % pid
    try:
        import psutil
        name = psutil.Process(pid).name()
    except Exception:
        pass
    proc_name_cache[pid] = name
    proc_name_ts[pid] = now
    return name


def writer_loop():
    while True:
        time.sleep(1)
        try:
            with stats_lock:
                items = [(pid, path, r, w) for (pid, path), (r, w) in stats.items() if r + w > 0]
                items.sort(key=lambda x: -(x[2] + x[3]))
                top = items[:500]
            rows = []
            for pid, path, r, w in top:
                rows.append({
                    "pid": pid,
                    "process": process_name(pid),
                    "file": path,
                    "read": r,
                    "write": w,
                    "total": r + w,
                })
            payload = {"running": True, "ts": int(time.time()), "rows": rows}
            tmp = LIVE_JSON + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, LIVE_JSON)
        except Exception:
            pass
        if os.path.exists(STOP_FILE):
            return


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
    except OSError:
        pass

    if not is_admin():
        payload = {"running": False,
                   "error": "需要管理员权限才能实时监控文件读写，请点击页面上的“以管理员身份启动”。"}
        with open(LIVE_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        sys.exit(3)

    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    build_device_map()

    from etw import ETW, ProviderInfo
    from etw.GUID import GUID
    from etw import evntrace as et

    provider = ProviderInfo(
        "Microsoft-Windows-Kernel-File",
        GUID("{EDD08927-9CC4-4E65-B970-C2560FB5C289}"),
        et.TRACE_LEVEL_INFORMATION,
        et.EVENT_TRACE_FLAG_FILE_IO,
        None,
    )

    tracer = ETW(
        session_name="NT Kernel Logger",
        providers=[provider],
        event_callback=on_event,
    )

    writer = threading.Thread(target=writer_loop, daemon=True)
    writer.start()

    try:
        tracer.start()
    except Exception as exc:
        payload = {"running": False,
                   "error": "启动 ETW 跟踪失败：%s（可能需要管理员权限）" % exc}
        with open(LIVE_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        sys.exit(2)

    try:
        while True:
            time.sleep(1)
            if os.path.exists(STOP_FILE):
                break
    finally:
        try:
            tracer.stop()
        except Exception:
            pass
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
