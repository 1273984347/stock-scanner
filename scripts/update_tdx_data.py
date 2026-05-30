"""更新通达信（TDX）日K线数据

从通达信官网下载全部 ZIP 数据包，解压到 TDX 目录。

下载页面：https://www.tdx.com.cn/article/alldata.html

用法：
    python scripts/update_tdx_data.py              # 增量更新（跳过无变化的包）
    python scripts/update_tdx_data.py --force       # 强制重新下载全部包
    python scripts/update_tdx_data.py --package shlday  # 只下载单个包
    python scripts/update_tdx_data.py --list        # 列出所有包
    python scripts/update_tdx_data.py --verify       # 验证已下载数据

包分类：
    realtime   - 每个交易日更新（shlday/szlday/hsjday/tdxgp/ScJyData_zbca 等）
    historical - 历史数据包，一次下载后长期有效（33day/34day/38day）
"""

import argparse
import logging
import os
import shutil
import struct
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlretrieve, urlopen, Request
import zipfile
import json


def _get_day_fmt(filename: str) -> str:
    """根据文件名判断 TDX .day 文件格式

    - 数字开头（基金/期权/宏观等）：<IfffffII（浮点元）
    - sh/sz/bj/tdx/xsb/csi/ggt 等前缀：<IiiiiiII（整数分）
    """
    if filename[0].isdigit():
        return '<IfffffII'
    return '<IiiiiiII'


def read_day_file(path: Path) -> list:
    """读取 TDX .day 文件，返回 [(date, open, high, low, close, amount, volume), ...]

    支持两种格式：
    - 股票/指数：价格存储为整数分（÷100），格式 <IiiiiiII
    - 基金/期权/宏观：价格存储为浮点数（元），格式 <IfffffII
    """
    fmt = _get_day_fmt(path.name)

    records = []
    with open(path, 'rb') as f:
        while True:
            data = f.read(32)
            if not data or len(data) < 32:
                break
            rec = struct.unpack(fmt, data)
            date = rec[0]
            if date < 19900101 or date > 21000101:
                continue
            if fmt == '<IiiiiiII':
                prices = [p / 100.0 for p in rec[1:5]]
            else:
                prices = list(rec[1:5])
            records.append((date, *prices, rec[5], rec[6]))
    return records


def verify_day_file(path: Path) -> dict:
    """验证单个 .day 文件，返回概要信息"""
    try:
        recs = read_day_file(path)
        if not recs:
            return {'ok': False, 'error': '无数据或格式错误'}
        last = recs[-1]
        return {
            'ok': True,
            'records': len(recs),
            'start_date': recs[0][0],
            'end_date': last[0],
            'last_close': last[4],
            'last_volume': last[6],
        }
    except Exception as e:
        return {'ok': False, 'error': str(e)}

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# 环境变量
TDX_DATA_PATH = os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/update_tdx.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# TDX 官方下载链接
TDX_BASE_URL = "https://www.tdx.com.cn/products/data/data/vipdoc"

# 全部数据包定义
# key: 包名(去掉.zip), name: 中文名, subdir: 解压目标子目录
# =====================================================================
# 下载配置（已禁用大量包，仅保留高频/统计数据）
# 如需恢复，参考 archive/PACKAGES_full.py
# =====================================================================
PACKAGES = {
    # ---- 高频/统计（当前保留）----
    "tdxgp": {
        "name": "高频数据(Tick)",
        "subdir": "gp",
        "zip": "tdxgp.zip",
        "data_type": "realtime",
        "file_pattern": "*.dat",
    },
    "ScJyData_zbca": {
        "name": "市场统计汇总",
        "subdir": "ds",
        "zip": "ScJyData_zbca.zip",
        "data_type": "realtime",
        "file_format": "csv",
    },

    # ---- 以下已禁用 ----
    # # ---- 日线（主要市场）----
    # "shlday":  {"name": "上证所有证券日线(TCKV4=0)",  "subdir": "sh/lday",   "zip": "shlday.zip"},
    # "szlday":  {"name": "深证所有证券日线(TCKV4=0)",  "subdir": "sz/lday",   "zip": "szlday.zip"},
    # "bjlday":  {"name": "北证所有证券日线",           "subdir": "bj/lday",   "zip": "bjlday.zip"},
    # # ---- 指数日线----
    # "shzsday": {"name": "上证指数日线",               "subdir": "sh/zs",     "zip": "shzsday.zip"},
    # "szzsday": {"name": "深证指数日线",               "subdir": "sz/zs",     "zip": "szzsday.zip"},
    # "tdxzs_day": {"name": "通达信板块指数日线",       "subdir": "tdx/zs",    "zip": "tdxzs_day.zip"},
    # # ---- 前复权日线----
    # "shlday_4": {"name": "上证所有证券日线(TCKV4=1)","subdir": "sh/lday_4", "zip": "shlday_4.zip"},
    # "szlday_4": {"name": "深证所有证券日线(TCKV4=1)","subdir": "sz/lday_4", "zip": "szlday_4.zip"},
    # # ---- 期权----
    # "shqqday": {"name": "上海股票期权日线",           "subdir": "sh/option", "zip": "shqqday.zip"},
    # "szqqday": {"name": "深圳股票期权日线",           "subdir": "sz/option", "zip": "szqqday.zip"},
    # # ---- 其他市场----
    # "xsbday":  {"name": "股转系统日线",              "subdir": "xsb/lday",  "zip": "xsbday.zip"},
    # "62day":   {"name": "中证指数日线",              "subdir": "csi/zs",    "zip": "62day.zip"},
    # "ggtday":  {"name": "港股通日线",                "subdir": "ggt/lday",  "zip": "ggtday.zip"},
    # # ---- 基金----
    # "33day":   {"name": "开放式基金日线",            "subdir": "fund/open",  "zip": "33day.zip",  "data_type": "historical"},
    # "34day":   {"name": "货币型基金日线",            "subdir": "fund/money", "zip": "34day.zip",  "data_type": "historical"},
    # # ---- 宏观----
    # "38day":   {"name": "宏观指标日线",              "subdir": "macro/lday", "zip": "38day.zip", "data_type": "historical"},
    # # ---- 财务数据----
    # "tdxfin":  {"name": "财务报表数据",               "subdir": "cw",         "zip": "tdxfin.zip", "data_type": "historical", "file_pattern": "*.dat"},
    # # ---- 沪股通----
    # "hsjday":  {"name": "沪股通日线",               "subdir": "hsj",        "zip": "hsjday.zip", "data_type": "realtime", "file_pattern": "**/*.day"},
}


# 增量更新元数据
META_FILE = Path("D:/TDX/update_meta.json")


def load_meta() -> dict:
    """加载更新元数据"""
    if META_FILE.exists():
        try:
            return json.loads(META_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_meta(meta: dict):
    """保存更新元数据"""
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def get_local_latest_date(pkg: dict) -> str | None:
    """获取本地数据的最新日期（格式 YYYYMMDD）"""
    subdir = Path(TDX_DATA_PATH) / pkg["subdir"]
    file_format = pkg.get("file_format", "")
    file_pattern = pkg.get("file_pattern", "")

    if file_format == "csv":
        # CSV 包：在目录中找唯一的 .csv 文件
        csv_files = list(subdir.glob("*.csv")) if subdir.exists() else []
        if not csv_files:
            return None
        last_file = max(csv_files, key=lambda f: f.stat().st_mtime)
        try:
            # CSV 第一列是 date，格式 YYYY_MM_DD
            # 文件按日期升序排列（最旧的在最后），找最大日期
            with open(last_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) < 2:
                return None
            max_date = "00000000"
            for row in lines[1:]:  # 跳过表头
                date_str = row.strip().split(",")[0].replace("_", "")
                if date_str > max_date:
                    max_date = date_str
            return max_date if max_date != "00000000" else None
        except Exception:
            return None

    # 处理嵌套目录（如 hsjday 的 sh/lday, sz/lday）
    if file_pattern == "**/*.day":
        all_files = list(subdir.glob("**/*.day")) if subdir.exists() else []
    else:
        all_files = list(subdir.glob("*.day")) if subdir.exists() else []
    if not all_files:
        return None
    # 取最新一条记录
    last_file = max(all_files, key=lambda f: f.stat().st_mtime)
    info = verify_day_file(last_file)
    return str(info.get("end_date")) if info.get("ok") else None


def check_remote_modified(url: str) -> datetime | None:
    """用 HTTP HEAD 请求检查服务器文件最后修改时间"""
    try:
        req = Request(url, method="HEAD")
        with urlopen(req, timeout=15) as resp:
            last_mod = resp.headers.get("Last-Modified")
            if last_mod:
                from email.utils import parsedate_to_datetime
                return parsedate_to_datetime(last_mod)
    except Exception as e:
        logger.debug(f"  HEAD 检查失败（将强制下载）: {e}")
    return None


def is_update_needed(key: str, pkg: dict, remote_mtime: datetime | None, force: bool = False) -> tuple[bool, str]:
    """判断是否需要更新此包

    返回 (是否需要, 原因说明)
    """
    if force:
        return True, "强制更新"

    meta = load_meta()
    local_info = meta.get(key, {})
    data_type = pkg.get("data_type", "realtime")

    # 无本地记录 → 需要更新
    if not local_info:
        return True, "首次更新"

    local_date = local_info.get("date")  # 本地数据最新日期
    local_mtime = local_info.get("mtime")  # 上次下载的 zip 时间

    # historical 类型：数据稳定，一次下载后长期有效，跳过每日检查
    if data_type == "historical":
        return False, f"历史数据包（本地: {local_date}），跳过每日检查"

    # 无法获取本地日期 → 仍需更新
    if not local_date:
        return True, "无本地日期记录"

    # 服务器未返回修改时间 → 跳过
    if remote_mtime is None:
        return False, "服务器不支持时间检查，跳过"

    # 服务器 zip 未更新 → 跳过
    if local_mtime:
        import email.utils
        try:
            remote_dt = email.utils.parsedate_to_datetime(remote_mtime) if isinstance(remote_mtime, str) else remote_mtime
            local_dt = datetime.fromisoformat(local_mtime) if isinstance(local_mtime, str) else local_mtime
            if remote_dt <= local_dt:
                return False, f"服务器无新数据（本地: {local_date}，服务器未更新）"
        except Exception:
            pass

    return True, f"本地数据: {local_date}，服务器有新数据"


def download_with_progress(url: str, dest: Path):
    """下载文件并显示进度"""
    def reporthook(block_num, block_size, total_size):
        if total_size > 0:
            pct = min(100, block_num * block_size * 100 // total_size)
            if block_num % 500 == 0:
                print(f"\r  下载进度: {pct}%", end="", flush=True)

    print(f"  下载: {url}")
    print(f"  保存: {dest}")

    if dest.exists():
        dest.unlink()

    try:
        urlretrieve(url, str(dest), reporthook)
        print()
        return True
    except Exception as e:
        print(f"\n  下载失败: {e}")
        if dest.exists():
            dest.unlink()
        return False


def extract_zip(zip_path: Path, pkg: dict) -> int:
    """解压 ZIP 文件到目标子目录，返回解压文件数"""
    if not zip_path.exists():
        return 0

    target_subdir = pkg["subdir"]
    target_dir = Path(TDX_DATA_PATH) / target_subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    file_format = pkg.get("file_format", "")
    file_pattern = pkg.get("file_pattern", "*.day")

    count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if file_format == "csv" and name.endswith(".csv"):
                target_path = target_dir / name
                with zf.open(name) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                count += 1
            elif file_pattern == "*.dat" and name.endswith(".dat"):
                target_path = target_dir / name
                with zf.open(name) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                count += 1
            elif name.endswith(".day"):
                target_path = target_dir / name
                with zf.open(name) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                count += 1

    return count


def update_package(key: str, pkg: dict, tmp_dir: Path, force: bool = False) -> int:
    """下载并更新单个数据包（增量更新）"""
    zip_name = pkg["zip"]
    zip_url = f"{TDX_BASE_URL}/{zip_name}"
    zip_path = tmp_dir / zip_name

    logger.info(f"\n{'='*50}")
    logger.info(f"检查: {pkg['name']} ({key})")
    logger.info(f"{'='*50}")

    # 1. 检查服务器是否有更新
    remote_mtime = None if force else check_remote_modified(zip_url)
    needed, reason = is_update_needed(key, pkg, remote_mtime, force)

    if not needed:
        local_date = load_meta().get(key, {}).get("date", "未知")
        logger.info(f"  跳过: {reason}")
        logger.info(f"  本地最新: {local_date}")
        return 0

    logger.info(f"  需更新: {reason}")

    # 2. 下载
    start = time.time()
    ok = download_with_progress(zip_url, zip_path)
    if not ok:
        logger.error(f"  下载失败，跳过")
        return 0

    download_time = time.time() - start

    # 3. 解压
    extract_start = time.time()
    file_count = extract_zip(zip_path, pkg)
    extract_time = time.time() - extract_start

    file_ext = pkg.get("file_format", "csv") if pkg.get("file_format") == "csv" else (pkg.get("file_pattern", "*.day").lstrip("*"))
    logger.info(f"  解压: {file_count} 个 .{file_ext} 文件")
    logger.info(f"  下载耗时: {download_time:.1f}s")
    logger.info(f"  解压耗时: {extract_time:.1f}s")

    # 4. 验证
    target_dir = Path(TDX_DATA_PATH) / pkg["subdir"]
    file_format = pkg.get("file_format", "")
    file_pattern = pkg.get("file_pattern", "")
    if file_format == "csv":
        actual = len(list(target_dir.glob("*.csv"))) if target_dir.exists() else 0
    elif file_pattern == "*.dat":
        actual = len(list(target_dir.glob("*.dat"))) if target_dir.exists() else 0
    elif file_pattern == "**/*.day":
        actual = len(list(target_dir.glob("**/*.day"))) if target_dir.exists() else 0
    else:
        actual = len(list(target_dir.glob("*.day"))) if target_dir.exists() else 0
    logger.info(f"  验证: {actual} 个文件")

    # 5. 清理 ZIP
    zip_path.unlink()

    # 6. 记录元数据
    meta = load_meta()
    local_date = get_local_latest_date(pkg)
    mtime_str = remote_mtime.isoformat() if remote_mtime else datetime.now().isoformat()
    meta[key] = {
        "date": local_date,
        "mtime": mtime_str,
        "updated_at": datetime.now().isoformat(),
    }
    save_meta(meta)

    return file_count


def main():
    parser = argparse.ArgumentParser(description="从通达信官网下载日K线数据")
    parser.add_argument("--package", help="只下载指定包（如 shlday）")
    parser.add_argument("--list", action="store_true", help="列出所有包")
    parser.add_argument("--verify", action="store_true", help="验证所有已下载数据")
    parser.add_argument("--force", action="store_true", help="强制重新下载所有包（忽略增量检查）")
    args = parser.parse_args()

    logger.info(f"TDX_DATA_PATH: {TDX_DATA_PATH}")
    logger.info(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.list:
        print("\n可用数据包：")
        for i, (k, v) in enumerate(PACKAGES.items(), 1):
            print(f"  {i:2d}. {k:15s} {v['name']}")
        return

    if args.verify:
        print("\n验证所有数据包：")
        print(f"{'包名':15s} {'子目录':25s} {'文件数':>8s} {'最新日期':>10s} {'最新收盘':>12s} {'状态'}")
        print("-" * 80)
        for key, pkg in PACKAGES.items():
            subdir = Path(TDX_DATA_PATH) / pkg["subdir"]
            file_format = pkg.get("file_format", "")
            file_pattern = pkg.get("file_pattern", "")
            if file_format == "csv":
                files = list(subdir.glob("*.csv")) if subdir.exists() else []
            elif file_pattern == "*.dat":
                files = list(subdir.glob("*.dat")) if subdir.exists() else []
            elif file_pattern == "**/*.day":
                files = list(subdir.glob("**/*.day")) if subdir.exists() else []
            else:
                files = list(subdir.glob("*.day")) if subdir.exists() else []
            if not files:
                print(f"{key:15s} {pkg['subdir']:25s} {'0':>8s} {'无数据':>10s} {'':>12s} {'无数据'}")
                continue
            # 抽样验证第一条和最后一条
            sample = files[0]
            if file_format == "csv":
                # CSV 包只做基本信息检查
                try:
                    with open(sample, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    # CSV 按日期升序，找最大日期
                    max_date = max(
                        (ln.split(",")[0].replace("_", "") for ln in lines[1:] if ln.strip()),
                        default="?"
                    )
                    print(f"{key:15s} {pkg['subdir']:25s} {len(files):>8d} {max_date:>10s} {'':>12s} CSV OK")
                except Exception as e:
                    print(f"{key:15s} {pkg['subdir']:25s} {len(files):>8d} {'?':>10s} {'':>12s} ERR: {e}")
                continue
            info = verify_day_file(sample)
            if not info['ok']:
                print(f"{key:15s} {pkg['subdir']:25s} {len(files):>8d} {'格式错误':>10s} {'':>12s} {info.get('error')}")
                continue
            last_file = files[-1]
            last_info = verify_day_file(last_file)
            status = "OK" if last_info.get('ok') else f"ERR: {last_info.get('error')}"
            last_close = f"{last_info.get('last_close', '?'):.4f}" if last_info.get('ok') else "?"
            print(f"{key:15s} {pkg['subdir']:25s} {len(files):>8d} {str(last_info.get('end_date', '?')):>10s} {last_close:>12s} {status}")
        return

    # 确定要下载的包
    if args.package:
        if args.package not in PACKAGES:
            logger.error(f"未知包: {args.package}")
            return
        packages_to_download = {args.package: PACKAGES[args.package]}
    else:
        packages_to_download = PACKAGES

    tmp_dir = Path("D:/TDX/temp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    for key, pkg in packages_to_download.items():
        try:
            total_files += update_package(key, pkg, tmp_dir, force=args.force)
        except Exception as e:
            logger.error(f"  处理 {key} 时出错: {e}")

    # 清理临时目录
    if tmp_dir.exists() and not any(tmp_dir.iterdir()):
        tmp_dir.rmdir()

    logger.info(f"\n完成: 共更新 {total_files} 个文件")


if __name__ == "__main__":
    main()
