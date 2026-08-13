# -*- coding: utf-8 -*-
"""
Disk I/O Monitor - 数据采集器（按进程统计磁盘/文件读写量）

用法：
    python collector.py                # 持续运行（每 5 秒采样，每 60 秒落库）
    python collector.py --test 30      # 自检：运行 30 秒后退出

数据写入：data/disk_io.db
日志写入：data/collector.log
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime

import psutil

from paths import BASE_DIR, DATA_DIR, DB_PATH, PID_COLLECTOR as PID_FILE, LOG_COLLECTOR as LOG_FILE, acquire_mutex

SAMPLE_INTERVAL = 5      # 默认采样间隔（秒）
FLUSH_INTERVAL = 60      # 默认落库间隔（秒）
MAX_GAP = 60             # 两次采样间隔超过该秒数，则丢弃该段增量（休眠/卡顿）
MAX_BURST = 50 * 1024 ** 3   # 单个进程单次采样增量上限（50GB），防误统计


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS process_io (
            day         TEXT NOT NULL,
            process     TEXT NOT NULL,
            read_bytes  INTEGER NOT NULL DEFAULT 0,
            write_bytes INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, process)
        );
        CREATE TABLE IF NOT EXISTS disk_io (
            day         TEXT NOT NULL,
            disk        TEXT NOT NULL,
            read_bytes  INTEGER NOT NULL DEFAULT 0,
            write_bytes INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, disk)
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


def flush(acc_proc, acc_disk):
    """把内存中的累计增量写进数据库；全部提交成功后才清空内存。"""
    if not acc_proc and not acc_disk:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        init_db(conn)
        with conn:
            for (day, name), (r, w) in acc_proc.items():
                if r <= 0 and w <= 0:
                    continue
                conn.execute(
                    "INSERT INTO process_io (day, process, read_bytes, write_bytes) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(day, process) DO UPDATE SET "
                    "read_bytes = read_bytes + excluded.read_bytes, "
                    "write_bytes = write_bytes + excluded.write_bytes",
                    (day, name, int(r), int(w)),
                )
            for (day, disk), (r, w) in acc_disk.items():
                if r <= 0 and w <= 0:
                    continue
                conn.execute(
                    "INSERT INTO disk_io (day, disk, read_bytes, write_bytes) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(day, disk) DO UPDATE SET "
                    "read_bytes = read_bytes + excluded.read_bytes, "
                    "write_bytes = write_bytes + excluded.write_bytes",
                    (day, disk, int(r), int(w)),
                )
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('last_update', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
            )
    finally:
        conn.close()
    acc_proc.clear()
    acc_disk.clear()


def sample_processes():
    """返回 {pid: (进程名, 累计读字节, 累计写字节)}"""
    out = {}
    for p in psutil.process_iter(["pid", "name", "io_counters"]):
        try:
            info = p.info
            io = info.get("io_counters")
            if io is None:
                continue
            out[info["pid"]] = (
                info.get("name") or "unknown",
                io.read_bytes,
                io.write_bytes,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return out


def sample_disks():
    """返回 {磁盘名: (累计读字节, 累计写字节)}"""
    try:
        disks = psutil.disk_io_counters(perdisk=True) or {}
    except Exception:
        return {}
    return {k: (v.read_bytes, v.write_bytes) for k, v in disks.items()}


def acquire_pid():
    os.makedirs(DATA_DIR, exist_ok=True)
    if _is_already_running():
        log("已有一个监控程序在运行，本实例退出（单实例限制）。")
        sys.exit(0)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def _is_already_running():
    # Single-instance check: named mutex first, PID file as fallback.
    if os.name == "nt":
        got = acquire_mutex("Local\\DiskIOMonitorCollector")
        if got is not None:
            return not got
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, encoding="utf-8") as f:
                old = int(f.read().strip())
            if psutil.pid_exists(old) and _pid_is_monitor(old):
                return True
        except (ValueError, OSError):
            pass
    return False

def _pid_is_monitor(pid):
    """确认该 PID 确实属于本程序（避免 PID 被系统复用后误判）。"""
    try:
        name = psutil.Process(pid).name().lower()
        return name in ("collector.exe", "python.exe", "pythonw.exe")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
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


def run(duration=None, interval=SAMPLE_INTERVAL, flush_interval=FLUSH_INTERVAL):
    prev_proc, prev_disk = {}, {}
    acc_proc, acc_disk = {}, {}
    last_sample = time.time()
    last_flush = time.time()
    start = time.time()
    log("开始监控（采样 %gs / 落库 %ds%s）"
        % (interval, flush_interval, " / 自检模式" if duration else ""))
    try:
        while True:
            now = time.time()
            gap = now - last_sample
            last_sample = now

            cur_proc = sample_processes()
            cur_disk = sample_disks()

            if gap <= MAX_GAP:
                day = datetime.now().strftime("%Y-%m-%d")
                for pid, (name, r, w) in cur_proc.items():
                    prev = prev_proc.get(pid)
                    if prev is None:
                        continue
                    dr, dw = r - prev[1], w - prev[2]
                    if dr < 0 or dw < 0 or dr + dw > MAX_BURST:
                        continue
                    key = (day, name)
                    acc = acc_proc.setdefault(key, [0, 0])
                    acc[0] += dr
                    acc[1] += dw
                for disk, (r, w) in cur_disk.items():
                    prev = prev_disk.get(disk)
                    if prev is None:
                        continue
                    dr, dw = r - prev[0], w - prev[1]
                    if dr < 0 or dw < 0 or dr + dw > MAX_BURST * 8:
                        continue
                    key = (day, disk)
                    acc = acc_disk.setdefault(key, [0, 0])
                    acc[0] += dr
                    acc[1] += dw

            prev_proc, prev_disk = cur_proc, cur_disk

            flush_req = os.path.join(DATA_DIR, "flush.request")
            if os.path.exists(flush_req):
                try:
                    os.remove(flush_req)
                except OSError:
                    pass
                n_proc, n_disk = len(acc_proc), len(acc_disk)
                flush(acc_proc, acc_disk)
                log("收到立即记录请求，已写入数据库（%d 个进程 / %d 个磁盘）" % (n_proc, n_disk))

            if now - last_flush >= flush_interval:
                n_proc, n_disk = len(acc_proc), len(acc_disk)
                flush(acc_proc, acc_disk)
                last_flush = now
                log("数据已写入数据库（%d 个进程 / %d 个磁盘）" % (n_proc, n_disk))

            if duration is not None and now - start >= duration:
                break
            time.sleep(max(0.5, interval - (time.time() - now)))
    except KeyboardInterrupt:
        log("收到退出信号，正在保存剩余数据…")
    finally:
        flush(acc_proc, acc_disk)
        release_pid()
        log("采集结束")


def main():
    ap = argparse.ArgumentParser(description="按进程统计磁盘/文件读写量并写入 SQLite")
    ap.add_argument("--test", type=int, default=0, help="自检：运行 N 秒后退出")
    ap.add_argument("--interval", type=float, default=SAMPLE_INTERVAL, help="采样间隔（秒）")
    ap.add_argument("--flush", type=int, default=FLUSH_INTERVAL, help="落库间隔（秒）")
    args = ap.parse_args()
    acquire_pid()
    run(duration=args.test or None, interval=args.interval, flush_interval=args.flush)


if __name__ == "__main__":
    main()
