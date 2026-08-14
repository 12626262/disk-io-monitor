# -*- coding: utf-8 -*-
"""
生成可视化仪表盘 HTML（output/dashboard.html）

用法：
    python report.py            # 读取 data/disk_io.db，生成报告
    python report.py --days 7   # 只看最近 7 天
"""

import argparse
import html
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from paths import BASE_DIR, DB_PATH, REPORT_PATH as OUT_PATH

READ_COLOR = "#3b82f6"
WRITE_COLOR = "#f59e0b"
OTHER_COLOR = "#cbd5e1"
PALETTE = [
    "#3b82f6", "#f59e0b", "#10b981", "#8b5cf6", "#ef4444",
    "#06b6d4", "#f97316", "#84cc16", "#ec4899", "#64748b",
]


def fmt_bytes(n, digits=1):
    n = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return "%d B" % int(n)
            return ("%." + str(digits) + "f %s") % (n, unit)
        n /= 1024
    return "%.1f PB" % n


def load_data(db):
    proc = defaultdict(lambda: [0, 0])   # (day, process) -> [读, 写]
    disk = defaultdict(lambda: [0, 0])   # (day, disk)    -> [读, 写]
    last_update = ""
    if not os.path.exists(db):
        return proc, disk, last_update
    conn = sqlite3.connect(db)
    try:
        for day, name, r, w in conn.execute(
                "SELECT day, process, read_bytes, write_bytes FROM process_io"):
            acc = proc[(day, name)]
            acc[0] += r
            acc[1] += w
        for day, name, r, w in conn.execute(
                "SELECT day, disk, read_bytes, write_bytes FROM disk_io"):
            acc = disk[(day, name)]
            acc[0] += r
            acc[1] += w
        row = conn.execute("SELECT value FROM meta WHERE key='last_update'").fetchone()
        if row:
            last_update = row[0]
    finally:
        conn.close()
    return proc, disk, last_update


def nice_step(maxv, count=4):
    if maxv <= 0:
        return 1.0
    raw = maxv / count
    mag = 10 ** math.floor(math.log10(raw)) if raw >= 1 else 1.0
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def svg_open(width, height):
    return '<svg viewBox="0 0 %d %d" width="100%%" role="img" xmlns="http://www.w3.org/2000/svg">' % (width, height)


def grouped_bars_svg(days, reads, writes, width=960, height=360):
    """每日读取/写入分组柱状图"""
    n = len(days)
    ml, mr, mt, mb = 82, 20, 50, 60
    pw, ph = width - ml - mr, height - mt - mb
    maxv = max(list(reads) + list(writes) + [1])
    step = nice_step(maxv, 4)
    ticks = []
    v = 0.0
    while True:
        ticks.append(v)
        v += step
        if v > maxv and len(ticks) >= 2:
            break
    top = ticks[-1]
    group = pw / max(n, 1)
    bw = min(group * 0.32, 44)
    parts = [svg_open(width, height)]

    for t in ticks:
        yy = mt + ph - (t / top) * ph
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e5e7eb"/>'
                     % (ml, yy, width - mr, yy))
        parts.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11" fill="#6b7280">%s</text>'
                     % (ml - 8, yy + 4, fmt_bytes(t)))

    for i, d in enumerate(days):
        cx = ml + (i + 0.5) * group
        x0 = cx - bw - 2
        x1 = cx + 2
        for x, val, color, label in (
                (x0, reads[i], READ_COLOR, "读取"),
                (x1, writes[i], WRITE_COLOR, "写入")):
            h = (val / top) * ph if top else 0
            parts.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s" rx="2">'
                '<title>%s %s：%s</title></rect>'
                % (x, mt + ph - h, bw, h, color, d, label, fmt_bytes(val)))
        if n <= 14 or i % 2 == 0:
            parts.append(
                '<text x="%.1f" y="%d" text-anchor="middle" font-size="11" fill="#6b7280">%s</text>'
                % (cx, height - mb + 22, d[5:]))

    # 图例
    lx = ml
    for color, label in ((READ_COLOR, "读取"), (WRITE_COLOR, "写入")):
        parts.append('<rect x="%d" y="%d" width="12" height="12" fill="%s" rx="2"/>'
                     % (lx, mt - 32, color))
        parts.append('<text x="%d" y="%d" font-size="12" fill="#374151">%s</text>'
                     % (lx + 17, mt - 22, label))
        lx += 70
    parts.append('</svg>')
    return "".join(parts)


def stacked_bars_svg(days, series, width=960, height=360):
    """各应用每日读写合计堆叠图；series: [(名称, 颜色, [每日值])]"""
    n = len(days)
    ml, mr, mt, mb = 82, 20, 50, 60
    pw, ph = width - ml - mr, height - mt - mb
    day_totals = [sum(s[2][i] for s in series) for i in range(n)]
    maxv = max(day_totals + [1])
    step = nice_step(maxv, 4)
    ticks = []
    v = 0.0
    while True:
        ticks.append(v)
        v += step
        if v > maxv and len(ticks) >= 2:
            break
    top = ticks[-1]
    group = pw / max(n, 1)
    bw = min(group * 0.62, 60)
    parts = [svg_open(width, height)]

    for t in ticks:
        yy = mt + ph - (t / top) * ph
        parts.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#e5e7eb"/>'
                     % (ml, yy, width - mr, yy))
        parts.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11" fill="#6b7280">%s</text>'
                     % (ml - 8, yy + 4, fmt_bytes(t)))

    for i, d in enumerate(days):
        cx = ml + (i + 0.5) * group
        y0 = mt + ph
        for label, color, values in series:
            val = values[i]
            if val <= 0:
                continue
            h = (val / top) * ph if top else 0
            parts.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s">'
                '<title>%s %s：%s</title></rect>'
                % (cx - bw / 2, y0 - h, bw, h, color, label, d[5:], fmt_bytes(val)))
            y0 -= h
        if n <= 14 or i % 2 == 0:
            parts.append(
                '<text x="%.1f" y="%d" text-anchor="middle" font-size="11" fill="#6b7280">%s</text>'
                % (cx, height - mb + 22, d[5:]))

    # 图例（横向排列）
    lx = ml
    for label, color, _ in series:
        parts.append('<rect x="%d" y="%d" width="10" height="10" fill="%s" rx="2"/>'
                     % (lx, mt - 32, color))
        parts.append('<text x="%d" y="%d" font-size="11" fill="#374151">%s</text>'
                     % (lx + 14, mt - 23, html.escape(label)))
        lx += 28 + len(label) * 12
    parts.append('</svg>')
    return "".join(parts)


def share_bar(share_pct):
    w = max(2, int(share_pct * 1.2))
    return ('<span class="bar-bg"><span class="bar-fg" style="width:%dpx"></span></span> %.1f%%'
            % (w, share_pct))


def build_html(proc, disk, last_update, days_n=14):
    today_key = date.today().isoformat()
    day_totals = defaultdict(lambda: [0, 0])
    app_totals = defaultdict(lambda: [0, 0])
    per_day_apps = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for (day, name), (r, w) in proc.items():
        day_totals[day][0] += r
        day_totals[day][1] += w
        app_totals[name][0] += r
        app_totals[name][1] += w
        acc = per_day_apps[day][name]
        acc[0] += r
        acc[1] += w

    days = [(date.today() - timedelta(days=i)).isoformat()
            for i in range(days_n - 1, -1, -1)]
    reads = [day_totals[d][0] for d in days]
    writes = [day_totals[d][1] for d in days]

    today_rw = per_day_apps.get(today_key, {})
    today_r = sum(v[0] for v in today_rw.values())
    today_w = sum(v[1] for v in today_rw.values())
    total_r = sum(v[0] for v in day_totals.values())
    total_w = sum(v[1] for v in day_totals.values())
    monitored_days = len(day_totals)

    top_today = sorted(today_rw.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:15]
    top_all = sorted(app_totals.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:20]

    # 堆叠图：按累计总量取前 8 个应用，其余归入“其他”
    top_names = [name for name, _ in top_all[:8]]
    series = []
    for idx, name in enumerate(top_names):
        vals = []
        for d in days:
            a = per_day_apps[d].get(name)
            vals.append((a[0] + a[1]) if a else 0)
        series.append((name, PALETTE[idx % len(PALETTE)], vals))
    others_vals = []
    for i, d in enumerate(days):
        top_sum = sum(s[2][i] for s in series)
        others_vals.append(max(0, day_totals[d][0] + day_totals[d][1] - top_sum))
    series.append(("其他", OTHER_COLOR, others_vals))

    # 每日汇总表
    daily_rows = []
    for d in reversed(days):
        r, w = day_totals.get(d, [0, 0])
        apps = sorted(per_day_apps.get(d, {}).items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
        top_name = html.escape(apps[0][0]) if apps else "—"
        daily_rows.append((d, r, w, top_name))

    has_data = bool(proc)
    total_today = today_r + today_w
    total_all = total_r + total_w

    def app_rows(rows, denom):
        out = []
        for i, (name, (r, w)) in enumerate(rows, 1):
            share = (r + w) / denom * 100 if denom else 0
            out.append(
                "<tr>"
                "<td>%d</td>"
                '<td class="app">%s</td>'
                "<td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s</td>"
                "</tr>"
                % (i, html.escape(name), fmt_bytes(r), fmt_bytes(w),
                   fmt_bytes(r + w), share_bar(share)))
        return "".join(out)

    if has_data:
        chart1 = grouped_bars_svg(days, reads, writes)
        chart2 = stacked_bars_svg(days, series)
        table_today = app_rows(top_today, total_today)
        table_all = app_rows(top_all, total_all)
        table_daily = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (d, fmt_bytes(r), fmt_bytes(w), fmt_bytes(r + w), t)
            for d, r, w, t in daily_rows)
    else:
        chart1 = chart2 = ""
        table_today = table_all = table_daily = ""

    cards = [
        ("今日读取", fmt_bytes(today_r), "各进程读取字节合计"),
        ("今日写入", fmt_bytes(today_w), "各进程写入字节合计"),
        ("今日总 I/O", fmt_bytes(total_today), "读取 + 写入"),
        ("累计总 I/O", fmt_bytes(total_all), "全部历史数据"),
        ("监控天数", "%d 天" % monitored_days, "有数据记录的天数"),
    ]
    cards_html = "".join(
        '<div class="card"><div class="k">%s</div><div class="v">%s</div>'
        '<div class="s">%s</div></div>' % (k, v, s)
        for k, v, s in cards)

    empty_html = ('<div class="empty">暂无数据。请先运行 <b>开始监控.bat</b>（或 '
                  '<code>python collector.py</code>）采集一段时间后再生成报告。</div>')

    page = []
    page.append("<!DOCTYPE html>")
    page.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    page.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    page.append('<meta http-equiv="refresh" content="60">')
    page.append("<title>磁盘读写监控</title>")
    page.append("<style>")
    page.append(CSS)
    page.append("</style></head><body><div class='wrap'>")
    page.append("<header><h1>磁盘读写监控</h1>")
    page.append('<div class="sub">数据最后更新：%s　｜　数据文件：data/disk_io.db　｜　页面每 60 秒自动刷新</div></header>'
                % html.escape(last_update or "—"))
    page.append('<div class="quickbar"><button onclick="flushNow()">立即记录并刷新</button>'
                '<button onclick="clearRecords()" style="color:#dc2626;margin-left:4px">清空记录</button>'
                '<span id="flushMsg"></span></div>')
    page.append('<section class="cards">' + cards_html + "</section>")

    if has_data:
        page.append('<section class="panel"><h2>每日总读写量（近 %d 天）</h2>%s</section>'
                    % (days_n, chart1))
        page.append('<section class="panel"><h2>各应用读写量（近 %d 天，读 + 写，Top 8 + 其他）</h2>%s</section>'
                    % (days_n, chart2))
        page.append('<section class="panel"><h2>今日各应用明细（Top 15）</h2>')
        page.append('<table><thead><tr><th>#</th><th>应用</th><th>读取</th><th>写入</th>'
                    "<th>合计</th><th>今日占比</th></tr></thead><tbody>%s</tbody></table></section>"
                    % table_today)
        page.append('<section class="panel"><h2>历史累计 Top 20</h2>')
        page.append('<table><thead><tr><th>#</th><th>应用</th><th>总读取</th><th>总写入</th>'
                    "<th>总合计</th><th>累计占比</th></tr></thead><tbody>%s</tbody></table></section>"
                    % table_all)
        page.append('<section class="panel"><h2>每日汇总（近 %d 天）</h2>' % days_n)
        page.append('<table><thead><tr><th>日期</th><th>读取</th><th>写入</th>'
                    "<th>合计</th><th>当日读写最多的应用</th></tr></thead><tbody>%s</tbody></table></section>"
                    % table_daily)
    else:
        page.append('<section class="panel">' + empty_html + "</section>")

    page.append('<section class="panel" id="livepanel">')
    page.append('<h2>实时文件读写监控（按进程 × 文件）</h2>')
    page.append('<div class="livetoolbar">')
    page.append('<button id="liveBtn" onclick="liveToggle()">开始实时监控</button>')
    page.append('<button id="liveAdminBtn" style="display:none" onclick="liveStartAdmin()">以管理员身份启动</button>')
    page.append('<span id="liveStatus" class="livestatus">点击开始后实时显示各进程对每个文件的读写速度，再次点击停止；首次使用需管理员授权。</span>')
    page.append('</div>')
    page.append('<div id="liveWrap" style="display:none">')
    page.append('<table><thead><tr><th>进程</th><th>文件</th><th>读取速度</th><th>写入速度</th><th>合计速度</th></tr></thead>'
                '<tbody id="liveRows"></tbody></table>')
    page.append('</div>')
    page.append('</section>')
    page.append("<footer>")
    page.append("说明：进程读写量来自 Windows 进程 I/O 计数器（逻辑文件 I/O，包含缓存读写），"
                "用于判断“哪个应用在读写”；磁盘总量来自磁盘控制器计数器（物理 I/O）。"
                "两者口径不同，数值会有差异，属正常现象。")
    page.append("<br>报告由 report.py 每 60 秒自动重新生成；原始数据保存在 data/disk_io.db。")
    page.append("<script>")
    page.append(JS_TEXT)
    page.append("</script>")
    page.append("</footer></div></body></html>")
    return "".join(page)


JS_TEXT = '''
var liveTimer = null;
var liveHbt = null;
var liveOn = false;

if (location.protocol === 'file:') {
    var _b = document.createElement('div');
    _b.style.cssText = 'background:#fef3c7;color:#92400e;padding:10px 14px;border-radius:8px;margin-bottom:12px;';
    _b.textContent = '当前是用“打开文件”方式查看页面，按钮无法联网工作。请用浏览器访问 http://127.0.0.1:8787/dashboard.html';
    var _w = document.querySelector('.wrap');
    if (_w) _w.insertBefore(_b, _w.firstChild);
}

function _j(resp) {
    return resp.text().then(function (t) {
        try { return JSON.parse(t); }
        catch (e) { return {error: '服务返回了无法解析的响应(HTTP ' + resp.status + ')：' + t.slice(0, 200)}; }
    });
}

function fmtBytes(n) {
    if (n < 1024) return n + ' B';
    var u = ['KB', 'MB', 'GB', 'TB'];
    var i = -1;
    do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
    return n.toFixed(1) + ' ' + u[i];
}

function setLiveStatus(txt, color) {
    var el = document.getElementById('liveStatus');
    el.textContent = txt;
    el.style.color = color || '#64748b';
}

function liveRender(data) {
    var tb = document.getElementById('liveRows');
    tb.innerHTML = '';
    if (!data || !data.rows || !data.rows.length) {
        var tr = document.createElement('tr');
        var td = document.createElement('td');
        td.colSpan = 5;
        td.textContent = '暂无读写活动，正在监听…';
        td.style.textAlign = 'center';
        td.style.color = '#94a3b8';
        tr.appendChild(td);
        tb.appendChild(tr);
        return;
    }
    data.rows.forEach(function (row) {
        var tr = document.createElement('tr');
        var tdP = document.createElement('td');
        tdP.textContent = row.process + ' (PID ' + row.pid + ')';
        var tdF = document.createElement('td');
        tdF.textContent = row.file;
        tdF.className = 'livefile';
        var tdR = document.createElement('td');
        tdR.textContent = fmtBytes(row.read) + '/s';
        var tdW = document.createElement('td');
        tdW.textContent = fmtBytes(row.write) + '/s';
        var tdT = document.createElement('td');
        tdT.textContent = fmtBytes(row.total) + '/s';
        tr.appendChild(tdP);
        tr.appendChild(tdF);
        tr.appendChild(tdR);
        tr.appendChild(tdW);
        tr.appendChild(tdT);
        tb.appendChild(tr);
    });
}

function liveStart(admin) {
    fetch('/api/live/start' + (admin ? '?admin=1' : '')).then(function (r) {
        return _j(r);
    }).then(function (j) {
        if (j.error) {
            setLiveStatus(j.error, '#dc2626');
            if (j.needAdmin) {
                document.getElementById('liveAdminBtn').style.display = 'inline-block';
            }
            return;
        }
        document.getElementById('liveAdminBtn').style.display = 'none';
        liveOn = true;
        document.getElementById('liveBtn').textContent = '停止实时监控';
        document.getElementById('liveWrap').style.display = 'block';
        liveHbt = setInterval(function () {
            fetch('/api/live/heartbeat', {method: 'POST'}).catch(function () {});
        }, 2000);
        var meta = document.querySelector('meta[http-equiv="refresh"]');
        if (meta) meta.remove();
        setLiveStatus('正在实时监控…再次点击停止', '#059669');
        liveTimer = setInterval(function () {
            fetch('/api/live/data').then(function (r) { return _j(r); }).then(function (d) {
                if (d.error && d.running === false) {
                    clearInterval(liveTimer);
                    clearInterval(liveHbt);
                    liveHbt = null;
                    liveTimer = null;
                    liveOn = false;
                    document.getElementById('liveBtn').textContent = '开始实时监控';
                    setLiveStatus(d.error, '#dc2626');
                    return;
                }
                liveRender(d);
            }).catch(function () {});
        }, 1000);
    }).catch(function (e) {
        setLiveStatus('请求失败：' + (e && e.message ? e.message : e) + '。请确认监控服务已启动，且用 http://127.0.0.1:8787 打开页面', '#dc2626');
    });
}

function liveStartAdmin() {
    liveStart(true);
}

function liveToggle() {
    if (liveOn) {
        clearInterval(liveTimer);
        clearInterval(liveHbt);
        liveHbt = null;
        liveTimer = null;
        liveOn = false;
        document.getElementById('liveBtn').textContent = '开始实时监控';
        document.getElementById('liveWrap').style.display = 'none';
        setLiveStatus('已停止实时监控', '#64748b');
        fetch('/api/live/stop').catch(function () {});
    } else {
        liveStart(false);
    }
}

function clearRecords() {
    if (!window.confirm('确定要清空所有历史记录吗？此操作不可恢复。')) return;
    fetch('/api/clear', {method: 'POST'}).then(function (r) { return _j(r); }).then(function (j) {
        var el = document.getElementById('flushMsg');
        el.textContent = j.ok ? '已清空全部记录，正在刷新…' : '清空失败：' + (j.error || '');
        el.style.display = 'inline';
        el.style.color = j.ok ? '#059669' : '#dc2626';
        setTimeout(function () { location.reload(); }, 1200);
    }).catch(function (e) {
        var el = document.getElementById('flushMsg');
        el.textContent = '清空失败：' + (e && e.message ? e.message : e);
        el.style.display = 'inline';
        el.style.color = '#dc2626';
    });
}

function flushNow() {
    fetch('/api/flush', {method: 'POST'}).then(function (r) { return _j(r); }).then(function (j) {
        var el = document.getElementById('flushMsg');
        el.textContent = j.ok ? '已请求立即记录，正在刷新…' : '请求失败：' + (j.error || '');
        el.style.display = 'inline';
        setTimeout(function () { location.reload(); }, 1500);
    }).catch(function (e) {
        var el = document.getElementById('flushMsg');
        el.textContent = '请求失败：' + (e && e.message ? e.message : e);
        el.style.display = 'inline';
        el.style.color = '#dc2626';
    });
}
'''

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
       background: #f1f5f9; color: #1e293b; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 24px 16px 48px; }
header h1 { font-size: 24px; }
header .sub { color: #64748b; font-size: 13px; margin-top: 6px; }
.cards { display: flex; gap: 14px; flex-wrap: wrap; margin: 20px 0; }
.card { flex: 1 1 180px; background: #fff; border-radius: 12px; padding: 16px 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card .k { font-size: 12px; color: #64748b; }
.card .v { font-size: 22px; font-weight: 600; margin-top: 6px; }
.card .s { font-size: 12px; color: #94a3b8; margin-top: 4px; }
.panel { background: #fff; border-radius: 12px; padding: 20px 22px; margin-bottom: 18px;
         box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.panel h2 { font-size: 16px; margin-bottom: 14px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #eef2f7; text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { color: #64748b; font-weight: 600; background: #f8fafc; }
td.app { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-bg { display: inline-block; width: 120px; height: 8px; border-radius: 4px;
          background: #eef2f7; overflow: hidden; vertical-align: middle; margin-right: 6px; }
.bar-fg { display: block; height: 100%; border-radius: 4px; background: #3b82f6; }
footer { color: #94a3b8; font-size: 12px; line-height: 1.8; margin-top: 8px; }
.empty { color: #94a3b8; padding: 30px 0; text-align: center; }
code { background: #f1f5f9; padding: 1px 6px; border-radius: 4px; }
.livetoolbar { margin-bottom: 12px; }
.livetoolbar button, .quickbar button { margin-right: 8px; padding: 6px 14px;
  border: 1px solid #cbd5e1; border-radius: 6px; background: #fff; cursor: pointer; font-size: 13px; }
.livetoolbar button:hover, .quickbar button:hover { background: #f1f5f9; }
.livestatus { font-size: 12px; color: #64748b; margin-left: 8px; }
.livefile { max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.quickbar { margin: 10px 0; }
#flushMsg { font-size: 12px; color: #059669; margin-left: 8px; display: none; }
"""


def generate_report(db=None, out=None, days=14):
    """生成仪表盘 HTML，返回输出文件路径（供 serve 进程内调用）。"""
    db = db or DB_PATH
    out = out or OUT_PATH
    proc, disk, last_update = load_data(db)
    html_text = build_html(proc, disk, last_update, days_n=days)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_text)
    return out


def main():
    ap = argparse.ArgumentParser(description="生成磁盘读写监控仪表盘 HTML")
    ap.add_argument("--db", default=DB_PATH, help="数据库路径")
    ap.add_argument("--out", default=OUT_PATH, help="输出 HTML 路径")
    ap.add_argument("--days", type=int, default=14, help="展示最近多少天（默认 14）")
    args = ap.parse_args()
    out = generate_report(args.db, args.out, args.days)
    print("已生成报告：%s" % out)


if __name__ == "__main__":
    main()
