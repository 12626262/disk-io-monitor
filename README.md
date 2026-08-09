# 磁盘读写监控（Disk I/O Monitor）

Windows 本地监控小工具：持续采集**每天每个应用读写了多少数据**，并在浏览器里用图表可视化展示。

## 特性

- 按进程统计每天/每小时的读写字节数（读取、写入分开统计）
- 浏览器仪表盘：每日总量图、各应用堆叠图、今日明细、历史 Top20
- **单实例**：同一时间只允许一个采集进程，重复点击“开始监控”只会提示，不会重复启动
- **兼容 Windows 7 及以上**（64 位），用 Python 3.8 + PyInstaller 5.13.2 打包
- 免安装便携版：编译产物在 `bin\`，拷到目标电脑直接双击运行，无需安装 Python

## 快速开始

### 便携版（免安装，推荐）

1. 把整个 `bin` 文件夹拷到目标电脑（64 位 Win7~Win11）
2. 双击 `bin\开始监控.bat`
3. 浏览器自动打开 http://127.0.0.1:8787/dashboard.html
4. 停止：双击 `bin\停止监控.bat`

### 源码运行（开发用）

```bat
conda activate disk-io-monitor
python collector.py          :: 采集器
python serve.py              :: 仪表盘服务（端口 8787）
python report.py             :: 手动生成报告
```

## 开发环境（Windows 7 兼容）

- conda 环境：`disk-io-monitor`（Python 3.8，位于 `G:\Conda\Anaconda\envs\disk-io-monitor`）
- 依赖：`psutil 5.9`、`PyInstaller 5.13.2`（均可用 conda 安装）
- 重新打包：运行 `build.bat`，产物输出到 `bin\`

> 说明：Python 3.9 及以上官方不再支持 Windows 7，所以兼容版必须用 Python 3.8。

## 单实例机制

- `collector.exe` 启动时会先获取 Windows 命名互斥锁（`Local\DiskIOMonitorCollector`），
  已有实例在运行则直接退出并写日志
- `开始监控.bat` 启动前还会检查 PID 文件 + 进程名，已在运行时提示“监控程序已经在运行中”，不重复启动
- `serve.exe`（仪表盘服务）同样有单实例互斥锁

## 文件结构

```
disk-io-monitor/
├─ collector.py        数据采集器（按进程、按磁盘统计读写字节）
├─ report.py           生成可视化 HTML 报告（纯 SVG，无外部依赖）
├─ serve.py            本地网页服务，自动刷新报告
├─ paths.py            路径解析（exe 版自动使用 exe 所在目录）
├─ build.bat           用 conda 环境打包到 bin\
├─ bin/                便携版成品（collector/serve/report exe + 脚本）
├─ data/               运行时数据库 disk_io.db + 日志 + PID 文件（不上传）
└─ output/             运行时生成的 dashboard.html（不上传）
```

## 数据口径说明

- 进程读写量来自 Windows 进程 I/O 计数器（逻辑文件 I/O，含缓存读写），用于判断“哪个应用在读写”
- 磁盘读写量来自磁盘控制器计数器（物理 I/O）
- 两者口径不同、数值有差异，属正常现象
- 数据保存为 SQLite（`data\disk_io.db`），每 60 秒增量写入，退出时自动保存剩余数据

## 在这里强烈感谢GPT老师对UI的编写以及对监控行为的指导
