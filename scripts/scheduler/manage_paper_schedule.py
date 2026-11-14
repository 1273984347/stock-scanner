"""
Windows 任务计划程序管理 — Paper Trading 60 天验证
====================================================

用途：
  python manage_paper_schedule.py install --days 7     # 1 周验证（默认）
  python manage_paper_schedule.py install --days 60    # 60 天全期
  python manage_paper_schedule.py verify     # 验证任务存在 + 下次运行时间
  python manage_paper_schedule.py status     # 看最近 5 次运行历史
  python manage_paper_schedule.py uninstall  # 注销任务

设计：
  - 验证模式：end_boundary = 今天 + N 天
  - 工作日 16:00 触发（盘后）
  - 调用 bash scripts/run_daily_paper.sh
  - 失败后 30 分钟内自动停止
  - 网络可用时才运行（akshare 限流容错）

前置：
  - Git for Windows 已装（提供 bash.exe）
  - scripts/run_daily_paper.sh 可执行
  - 当前用户有"创建任务"权限
"""
import sys
import os
import json
import argparse
import subprocess
import shutil
from datetime import datetime, timedelta
from pathlib import Path

# ST20: Windows GBK 安全
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK_NAME = "StockScanner\\PaperTradingDaily"
TASK_DISPLAY = "Stock Scanner Paper Trading Daily"
BASH_CANDIDATES = [
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Windows\System32\bash.exe",
]
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
DEFAULT_TRIAL_DAYS = 7  # 1 周验证期默认


def detect_bash() -> str:
    """检测 bash.exe 路径 — 优先 Git Bash"""
    # 1. 试 where
    try:
        out = subprocess.check_output(["where", "bash"], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            line = line.strip()
            if "Git" in line and "bash.exe" in line:
                return line
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    # 2. 试常见路径
    for p in BASH_CANDIDATES:
        if Path(p).exists():
            return p
    raise FileNotFoundError("未找到 bash.exe，请安装 Git for Windows")


def generate_xml(start: datetime, end: datetime, bash_path: str) -> str:
    """生成 Windows Task Scheduler XML

    触发器：工作日 16:00
    动作：调用 bash scripts/run_daily_paper.sh
    设置：30 分钟超时、网络可用时才运行
    """
    start_str = start.strftime("%Y-%m-%dT16:00:00")
    end_str = end.strftime("%Y-%m-%dT23:59:59")
    # 批次 C / P2-13：XML 必须转义（路径含 & / < 会破坏任务定义）；
    # 旧实现的 `.replace("\\", "\\\\")` 是字符串字面量习惯，XML 里不需要，
    # 反而会在 WorkingDirectory 里留下双反斜杠。
    from xml.sax.saxutils import escape
    proj = escape(str(PROJECT_ROOT))
    bash_esc = escape(str(bash_path))

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{TASK_DISPLAY} - 60 day paper trading validation</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_str}</StartBoundary>
      <EndBoundary>{end_str}</EndBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday />
          <Tuesday />
          <Wednesday />
          <Thursday />
          <Friday />
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions>
    <Exec>
      <Command>{bash_esc}</Command>
      <Arguments>scripts/run_daily_paper.sh</Arguments>
      <WorkingDirectory>{proj}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(days: int = DEFAULT_TRIAL_DAYS) -> int:
    """注册任务（默认 7 天验证期）"""
    try:
        bash_path = detect_bash()
        print(f"  [OK] bash.exe: {bash_path}")
    except FileNotFoundError as e:
        print(f"  [ERR] {e}")
        return 1

    # 1 周=7 天 / 60 天全期
    if days == 7:
        period_label = "1 周验证"
    elif days == 60:
        period_label = "60 天全期"
    else:
        period_label = f"{days} 天"
    print(f"  [OK] 运行期: {period_label}")

    # 已存在则先删（schtasks /create 对同名任务会失败）
    check_cmd = ["schtasks", "/query", "/tn", TASK_NAME]
    check = subprocess.run(check_cmd, capture_output=True, text=True)
    if check.returncode == 0:
        print(f"  [INFO] 检测到已存在任务，先删除...")
        del_cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
        subprocess.run(del_cmd, capture_output=True, text=True)

    start = datetime.now() + timedelta(minutes=1)  # 1 分钟后开始
    end = datetime.now() + timedelta(days=days)
    print(f"  [OK] 边界: {start.strftime('%Y-%m-%d %H:%M')} - {end.strftime('%Y-%m-%d %H:%M')}")

    xml = generate_xml(start, end, bash_path)
    xml_path = LOG_DIR / f"paper_trading_task_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml"
    # Task Scheduler 期望 UTF-16 LE BOM
    with open(xml_path, "wb") as f:
        f.write(b"\xff\xfe")  # BOM
        f.write(xml.encode("utf-16-le"))
    print(f"  [OK] XML 已生成: {xml_path}")

    # 注册任务
    cmd = ["schtasks", "/create", "/xml", str(xml_path), "/tn", TASK_NAME]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [OK] 任务已注册: {TASK_NAME}")
            print()
            print("  后续步骤：")
            print(f"    1. 验证任务: python {Path(__file__).name} verify")
            print(f"    2. 手动跑一次: schtasks /run /tn \"{TASK_NAME}\"")
            print(f"    3. 1 周后看 tracking:")
            print(f"       python scripts/paper_vs_backtest.py --mode compare")
            print(f"    4. 验证通过后延长到 60 天: python {Path(__file__).name} install --days 60")
            print(f"    5. 不想跑了: python {Path(__file__).name} uninstall")
            return 0
        else:
            print(f"  [ERR] schtasks 失败: {result.stderr.strip()}")
            print(f"         提示: 需要管理员权限打开 PowerShell")
            return 2
    except FileNotFoundError:
        print("  [ERR] schtasks 不在 PATH")
        return 1


def verify() -> int:
    """验证任务存在 + 下次运行时间"""
    cmd = ["schtasks", "/query", "/tn", TASK_NAME, "/v", "/fo", "LIST"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [ERR] 任务不存在: {TASK_NAME}")
            print(f"         提示: 先跑 install")
            return 1
        print(result.stdout)
        return 0
    except FileNotFoundError:
        print("  [ERR] schtasks 不在 PATH")
        return 1


def status() -> int:
    """看最近 5 次运行历史"""
    cmd = ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [ERR] 任务不存在: {TASK_NAME}")
            return 1
        # 提取关键信息
        next_run = None
        last_run = None
        last_result = None
        for line in result.stdout.splitlines():
            if "Next Run Time:" in line:
                next_run = line.split(":", 1)[1].strip()
            elif "Last Run Time:" in line:
                last_run = line.split(":", 1)[1].strip()
            elif "Last Result:" in line:
                last_result = line.split(":", 1)[1].strip()
            elif "Status:" in line:
                status_val = line.split(":", 1)[1].strip()
        print(f"  任务: {TASK_NAME}")
        print(f"  状态: {status_val if 'status_val' in dir() else '?'}")
        print(f"  下次运行: {next_run or '?'}")
        print(f"  上次运行: {last_run or '从未'}")
        print(f"  上次结果: {last_result or '?'}  (0=成功 1=失败)")
        print()
        print("  详细日志: tail -50 logs/cron_paper.log")
        return 0
    except FileNotFoundError:
        print("  [ERR] schtasks 不在 PATH")
        return 1


def uninstall() -> int:
    """注销任务"""
    cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  [OK] 任务已注销: {TASK_NAME}")
            return 0
        else:
            print(f"  [ERR] {result.stderr.strip()}")
            return 1
    except FileNotFoundError:
        print("  [ERR] schtasks 不在 PATH")
        return 1


def main():
    parser = argparse.ArgumentParser(description="Windows Task Scheduler 管理 - Paper Trading 60 天验证")
    parser.add_argument("action", choices=["install", "verify", "status", "uninstall"],
                        help="install=注册; verify=查询配置; status=运行历史; uninstall=删除")
    parser.add_argument("--days", type=int, default=DEFAULT_TRIAL_DAYS,
                        help=f"运行天数（默认 {DEFAULT_TRIAL_DAYS} = 1 周; 60 = 全期）")
    args = parser.parse_args()

    print("=" * 60)
    print(f"  Paper Trading 调度器 - {args.action.upper()}")
    print("=" * 60)

    if args.action == "install":
        sys.exit(install(args.days))
    elif args.action == "verify":
        sys.exit(verify())
    elif args.action == "status":
        sys.exit(status())
    elif args.action == "uninstall":
        sys.exit(uninstall())


if __name__ == "__main__":
    main()
