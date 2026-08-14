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
HEARTBEAT_FILE = os.path.join(DATA_DIR, "live.heartbeat")
LOG_FILE = os.path.join(DATA_DIR, "filewatch.log")


def dlog(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_device_map = {}
_drive_letters_cache = None


def physical_drive_letters():
    """Map 'PhysicalDriveN' -> 'PhysicalDriveN (C: D:)' via IOCTL_STORAGE_GET_DEVICE_NUMBER.
    Returns None if the mapping is unavailable (then caller shows raw names)."""
    global _drive_letters_cache
    if _drive_letters_cache is not None:
        return _drive_letters_cache
    mapping = {}
    try:
        import struct
        GENERIC_READ = 0x80000000
        FILE_SHARE_ALL = 0x7
        OPEN_EXISTING = 3
        IOCTL_STORAGE_GET_DEVICE_NUMBER = 0x2D0000 | (0x30 << 2)  # CTL_CODE(0x2D, 0x30, 0, 0)
        by_disk = {}
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            h = ctypes.windll.kernel32.CreateFileW("\\\\.\\%s:" % letter, 0, FILE_SHARE_ALL,
                                                   None, OPEN_EXISTING, 0, None)
            if h in (None, -1, 0xFFFFFFFFFFFFFFFF):
                continue
            try:
                buf = ctypes.create_string_buffer(24)
                ret = ctypes.c_ulong(0)
                ok = ctypes.windll.kernel32.DeviceIoControl(h, IOCTL_STORAGE_GET_DEVICE_NUMBER,
                                                            None, 0, buf, 24, ctypes.byref(ret), None)
                if ok:
                    dev_type, dev_number, part_number = struct.unpack("III", buf.raw[:12])
                    by_disk.setdefault(dev_number, []).append(letter + ":")
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        for num in sorted(by_disk):
            letters = " ".join(by_disk[num])
            mapping["PhysicalDrive%d" % num] = "PhysicalDrive%d (%s)" % (num, letters)
    except Exception:
        return None
    _drive_letters_cache = mapping if mapping else {}
    return _drive_letters_cache


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
fileobj_path = {}
FILE_PROVIDER_GUID = "{EDD08927-9CC4-4E65-B970-C2560FB5C289}"
DISK_PROVIDER_GUID = "{C7BDE69A-E1E0-4177-B6EF-283AD1525271}"
DISK_CLASSIC_GUID = "{3D6FA8D4-FE05-11D0-9DDA-00C04FD7BA7C}"
disk_stats = defaultdict(lambda: [0, 0])  # pid -> [read, write] (physical disk)
disk_active_ts = {}  # pid -> last activity time
proc_io_speed = {}  # pid -> [read_bps, write_bps, process name]
disk_io_total_speed = []  # per-physical-disk totals: [{'name','read','write','total'}]
stats_lock = threading.Lock()
stats = defaultdict(lambda: [0, 0])  # (pid, path) -> [read, write]
active_ts = {}  # (pid, path) -> last activity time
counters = {"total": 0, "by_opcode": {}, "name_ok": 0, "io_ok": 0, "parse_err": 0,
            "io_missing_size": 0, "io_missing_path": 0,
            "disk_io_ok": 0, "disk_missing_size": 0, "by_provider": {}}


def on_event(event_tup):
    """ETW event callback: (event_id, parsed dict)"""
    try:
        event_id, ev = event_tup
        prov = str(ev.get("EventHeader", {}).get("ProviderId", "")).upper()
        opcode = ev.get("EventHeader", {}).get("EventDescriptor", {}).get("Opcode", 0)
        if event_id == 0 and opcode:
            event_id = opcode
        pid = ev["EventHeader"]["ProcessId"]
        ensure_pid_name(pid)
        with stats_lock:
            counters["total"] += 1
            counters["by_provider"][prov] = counters["by_provider"].get(prov, 0) + 1
            counters["by_opcode"][str(event_id)] = counters["by_opcode"].get(str(event_id), 0) + 1
        if counters["by_opcode"].get(str(event_id), 0) == 1 and event_id in (0, 10, 11, 12, 15, 16):
            dlog("first event id=%s opcode=%s provider=%s keys=%s FileKey=%s FileObject=%s SizeOfIo=%s ByteCount=%s TransferSize=%s FileName=%s"
                 % (event_id, opcode, prov, sorted(ev.keys()), ev.get("FileKey"), ev.get("FileObject"),
                    ev.get("SizeOfIo"), ev.get("ByteCount"), ev.get("TransferSize"), ev.get("FileName")))
        if DISK_PROVIDER_GUID.upper() in prov or DISK_CLASSIC_GUID.upper() in prov:
            # physical disk I/O attributed to the issuing process (matches Task Manager)
            size = to_int(ev.get("TransferSize"))
            if size is None:
                size = to_int(ev.get("ByteCount"))
            if size is None:
                size = to_int(ev.get("IoSize"))
            if size is None:
                size = to_int(ev.get("IOSize"))
            if size is None:
                size = to_int(ev.get("Size"))
            if size is None:
                with stats_lock:
                    counters["disk_missing_size"] += 1
                return
            idx = 0 if event_id == 10 else 1
            with stats_lock:
                disk_stats[pid][idx] += size
                disk_active_ts[pid] = time.time()
                counters["disk_io_ok"] += 1
            return
        if event_id in (10, 12):  # NameCreate/Create carry the file path
            fkey = to_int(ev.get("FileKey"))
            fobj = to_int(ev.get("FileObject"))
            fname = normalize_path(to_str(ev.get("FileName")))
            if fname:
                with stats_lock:
                    if len(filekey_path) > 60000:
                        filekey_path.clear()
                    if fkey is not None:
                        filekey_path[fkey] = fname
                    if fobj is not None:
                        fileobj_path[fobj] = fname
                    counters["name_ok"] += 1
        elif event_id in (15, 16):  # Read / Write events
            fkey = to_int(ev.get("FileKey"))
            fobj = to_int(ev.get("FileObject"))
            size = to_int(ev.get("IOSize"))
            if size is None:
                size = to_int(ev.get("ByteCount"))
            if size is None:
                size = to_int(ev.get("IoSize"))
            if size is None:
                size = to_int(ev.get("RequestedByteCount"))
            if size is None:
                with stats_lock:
                    counters["io_missing_size"] += 1
                return
            with stats_lock:
                path = filekey_path.get(fkey)
                if path is None and fobj is not None:
                    path = fileobj_path.get(fobj)
            if not path:
                path = "<unknown file>"
                with stats_lock:
                    counters["io_missing_path"] += 1
            key = (pid, path)
            with stats_lock:
                stats[key][0 if event_id == 15 else 1] += size
                active_ts[key] = time.time()
                counters["io_ok"] += 1
    except Exception:
        with stats_lock:
            counters["parse_err"] += 1


proc_name_cache = {}
proc_name_ts = {}
pid_names = {}
pid_names_ts = {}


def ensure_pid_name(pid):
    # resolve name at event time and cache it, so exited processes still show names
    now = time.time()
    if pid in pid_names and now - pid_names_ts.get(pid, 0) < 120:
        return
    try:
        import psutil
        pid_names[pid] = psutil.Process(pid).name()
    except Exception:
        pid_names[pid] = "PID %d" % pid
    pid_names_ts[pid] = now


def process_name(pid):
    now = time.time()
    if pid in pid_names and now - pid_names_ts.get(pid, 0) < 120:
        return pid_names[pid]
    name = "PID %d" % pid
    try:
        import psutil
        name = psutil.Process(pid).name()
    except Exception:
        pass
    pid_names[pid] = name
    pid_names_ts[pid] = now
    return name


SPEED_THRESHOLD = 512000  # 500 KB/s


def proc_io_loop():
    """Per-process disk speed fallback: Windows process I/O counters (the same
    counters Task Manager uses) plus physical disk totals (psutil)."""
    import psutil
    prev_proc = {}
    prev_disk = {}
    prev_ts = time.time()
    while True:
        time.sleep(1.0)
        now = time.time()
        dt = max(0.001, now - prev_ts)
        snap_proc = {}
        for p in psutil.process_iter(["pid", "name"]):
            try:
                io = p.io_counters()
                snap_proc[p.info["pid"]] = (io.read_bytes, io.write_bytes, p.info["name"])
            except Exception:
                pass
        speeds = {}
        for pid, (rb, wb, name) in snap_proc.items():
            if pid in prev_proc:
                prb, pwb, _ = prev_proc[pid]
                dr = max(0, rb - prb) / dt
                dw = max(0, wb - pwb) / dt
                if dr > 0 or dw > 0:
                    speeds[pid] = [dr, dw, name]
        disk_snap = {}
        try:
            for dname, d in psutil.disk_io_counters(perdisk=True).items():
                disk_snap[dname] = (d.read_bytes, d.write_bytes)
        except Exception:
            pass
        totals = []
        dmap = physical_drive_letters()
        for dname, (rb, wb) in disk_snap.items():
            if dname in prev_disk:
                prd = max(0, rb - prev_disk[dname][0]) / dt
                pwd = max(0, wb - prev_disk[dname][1]) / dt
                disp = dname
                if dmap and dname in dmap:
                    disp = dmap[dname]
                totals.append({"name": disp, "read": int(prd), "write": int(pwd),
                               "total": int(prd + pwd)})
        totals.sort(key=lambda x: -(x["total"]))
        with stats_lock:
            proc_io_speed.clear()
            proc_io_speed.update(speeds)
            disk_io_total_speed[:] = totals
        prev_proc = {pid: (rb, wb, name) for pid, (rb, wb, name) in snap_proc.items()}
        prev_disk = dict(disk_snap)
        prev_ts = now


def writer_loop():
    last_dbg = 0
    prev_snapshot = {}
    prev_ts = time.time()
    while True:
        time.sleep(1)
        now = time.time()
        elapsed = max(0.001, now - prev_ts)
        try:
            with stats_lock:
                snapshot = {k: list(v) for k, v in stats.items()}  # deep-copy values so deltas work
                active = dict(active_ts)
            rows = []
            for (pid, path), (r, w) in snapshot.items():
                if path.startswith(DATA_DIR):
                    continue
                if "EtwRTDiskIOMonitorFileTrace" in path:
                    continue
                if (pid, path) in prev_snapshot:
                    pr, pw = prev_snapshot[(pid, path)]
                    rs = max(0, r - pr) / elapsed
                    ws = max(0, w - pw) / elapsed
                else:
                    rs = 0
                    ws = 0
                if rs <= 0 and ws <= 0 and now - active.get((pid, path), 0) >= 3:
                    continue
                rows.append({
                    "pid": pid,
                    "process": process_name(pid),
                    "file": path,
                    "read": int(rs),
                    "write": int(ws),
                    "total": int(rs + ws),
                })
            rows.sort(key=lambda x: -(x["total"]))
            rows = rows[:300]
            with stats_lock:
                pio = {pid: list(v) for pid, v in proc_io_speed.items()}
                dtot = list(disk_io_total_speed)
            disk_rows = []
            for dp, (rs, ws, pname) in pio.items():
                if pname is None:
                    pname = "(PID %d)" % dp
                disk_rows.append({"pid": dp, "process": pname,
                                  "read": int(rs), "write": int(ws), "total": int(rs + ws)})
            disk_rows.sort(key=lambda x: -(x["total"]))
            disk_rows = disk_rows[:100]
            payload = {"running": True, "ts": int(now), "rows": rows, "disk_rows": disk_rows,
                       "disk_totals": dtot}
            prev_snapshot = snapshot
            prev_ts = now
            if now - last_dbg >= 5:
                with stats_lock:
                    dbg = {
                        "total": counters["total"],
                        "by_opcode": dict(counters["by_opcode"]),
                        "name_ok": counters["name_ok"],
                        "io_ok": counters["io_ok"],
                        "parse_err": counters["parse_err"],
                        "io_missing_size": counters["io_missing_size"],
                        "io_missing_path": counters["io_missing_path"],
                        "disk_io_ok": counters.get("disk_io_ok", 0),
                        "disk_missing_size": counters.get("disk_missing_size", 0),
                        "by_provider": dict(counters.get("by_provider", {})),
                        "filekey_map": len(filekey_path),
                        "stats_entries": len(stats),
                    }
                payload["debug"] = dbg
                dlog("debug %s" % dbg)
                last_dbg = now
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
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass

    build_device_map()

    # single-instance guard + clean up stale ETW session from abnormal exit
    from paths import acquire_mutex
    if acquire_mutex("Local\\DiskIOMonitorFilewatch") is False:
        payload = {"running": False, "error": "filewatch is already running (single instance)."}
        with open(LIVE_JSON, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        sys.exit(0)
    try:
        from etw.etw import TraceProperties
        from etw import evntrace as et
        et.ControlTraceW(et.TRACEHANDLE(0), "DiskIOMonitorFileTrace",
                         TraceProperties().get(), et.EVENT_TRACE_CONTROL_STOP)
        dlog("stopped stale ETW session DiskIOMonitorFileTrace")
    except Exception:
        pass

    from etw import ETW, ProviderInfo
    from etw.GUID import GUID
    from etw import evntrace as et

    file_provider = ProviderInfo(
        "Microsoft-Windows-Kernel-File",
        GUID(FILE_PROVIDER_GUID),
        5,  # TRACE_LEVEL_VERBOSE
        0xFFFFFFFF,
        None,
    )
    disk_provider = ProviderInfo(
        "Microsoft-Windows-Kernel-Disk",
        GUID(DISK_PROVIDER_GUID),
        5,
        0xFFFFFFFF,
        None,
    )

    tracer = ETW(
        session_name="DiskIOMonitorFileTrace",
        providers=[file_provider, disk_provider],
        event_callback=on_event,
        ignore_exists_error=False,
        event_id_filters=[0, 10, 11, 12, 15, 16],
        ring_buf_size=32768,
    )

    dlog("filewatch starting, admin ok, pid=%d" % os.getpid())

    writer = threading.Thread(target=writer_loop, daemon=True)
    writer.start()
    pio_thread = threading.Thread(target=proc_io_loop, daemon=True)
    pio_thread.start()

    try:
        tracer.start()
        dlog("ETW session started")
        time.sleep(3)
        try:
            alive = tracer.consumer.process_thread.is_alive()
            pt_err = getattr(tracer.consumer, "_pt_error", None)
            dlog("consumer alive=%s pt_error=%s kernel_was_running=%s"
                 % (alive, pt_err, tracer.provider.kernel_trace_was_running))
        except Exception as e:
            dlog("diag error: %s" % e)
            alive = True
            pt_err = None
        if not alive:
            payload = {"running": False,
                       "error": "ETW consumer thread exited (ProcessTrace error %s). Please stop and start again." % pt_err}
            with open(LIVE_JSON, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            sys.exit(2)
    except Exception as exc:
        dlog("ETW start failed: %s" % exc)
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
            try:
                if os.path.exists(HEARTBEAT_FILE):
                    if time.time() - os.path.getmtime(HEARTBEAT_FILE) > 10:
                        dlog("heartbeat expired, stopping")
                        break
                else:
                    dlog("no heartbeat file, stopping")
                    break
            except Exception:
                pass
    finally:
        stopper = threading.Thread(target=lambda: tracer.stop(), daemon=True)
        stopper.start()
        stopper.join(5)
        if stopper.is_alive():
            os._exit(0)
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
