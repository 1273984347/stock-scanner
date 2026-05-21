"""更新通达信（TDX）日K线数据

从通达信官网下载全部 ZIP 数据包，解压到 TDX 目录。

下载页面：https://www.tdx.com.cn/article/alldata.html

用法：
    python scripts/update_tdx_data.py              # 下载全部 16 个包
    python scripts/update_tdx_data.py --package shlday  # 只下载单个包
    python scripts/update_tdx_data.py --list       # 列出所有包
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
from urllib.request import urlretrieve
import zipfile


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
PACKAGES = {
    # ---- 日线（主要市场）----
    "shlday": {
        "name": "上证所有证券日线(TCKV4=0)",
        "subdir": "sh/lday",
        "zip": "shlday.zip",
    },
    "szlday": {
        "name": "深证所有证券日线(TCKV4=0)",
        "subdir": "sz/lday",
        "zip": "szlday.zip",
    },
    "bjlday": {
        "name": "北证所有证券日线",
        "subdir": "bj/lday",
        "zip": "bjlday.zip",
    },
    # ---- 指数日线----
    "shzsday": {
        "name": "上证指数日线",
        "subdir": "sh/zs",
        "zip": "shzsday.zip",
    },
    "szzsday": {
        "name": "深证指数日线",
        "subdir": "sz/zs",
        "zip": "szzsday.zip",
    },
    "tdxzs_day": {
        "name": "通达信板块指数日线",
        "subdir": "tdx/zs",
        "zip": "tdxzs_day.zip",
    },
    # ---- 前复权日线----
    "shlday_4": {
        "name": "上证所有证券日线(TCKV4=1)",
        "subdir": "sh/lday_4",
        "zip": "shlday_4.zip",
    },
    "szlday_4": {
        "name": "深证所有证券日线(TCKV4=1)",
        "subdir": "sz/lday_4",
        "zip": "szlday_4.zip",
    },
    # ---- 期权----
    "shqqday": {
        "name": "上海股票期权日线",
        "subdir": "sh/option",
        "zip": "shqqday.zip",
    },
    "szqqday": {
        "name": "深圳股票期权日线",
        "subdir": "sz/option",
        "zip": "szqqday.zip",
    },
    # ---- 其他市场----
    "xsbday": {
        "name": "股转系统日线",
        "subdir": "xsb/lday",
        "zip": "xsbday.zip",
    },
    "62day": {
        "name": "中证指数日线",
        "subdir": "csi/zs",
        "zip": "62day.zip",
    },
    "ggtday": {
        "name": "港股通日线",
        "subdir": "ggt/lday",
        "zip": "ggtday.zip",
    },
    # ---- 基金----
    "33day": {
        "name": "开放式基金日线",
        "subdir": "fund/open",
        "zip": "33day.zip",
    },
    "34day": {
        "name": "货币型基金日线",
        "subdir": "fund/money",
        "zip": "34day.zip",
    },
    # ---- 宏观----
    "38day": {
        "name": "宏观指标日线",
        "subdir": "macro/lday",
        "zip": "38day.zip",
    },
}


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


def extract_zip(zip_path: Path, target_subdir: str) -> int:
    """解压 ZIP 文件到目标子目录，返回解压文件数"""
    if not zip_path.exists():
        return 0

    target_dir = Path(TDX_DATA_PATH) / target_subdir
    target_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith(".day"):
                target_path = target_dir / name
                with zf.open(name) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                count += 1

    return count


def update_package(key: str, pkg: dict, tmp_dir: Path) -> int:
    """下载并更新单个数据包"""
    zip_name = pkg["zip"]
    zip_url = f"{TDX_BASE_URL}/{zip_name}"
    zip_path = tmp_dir / zip_name

    logger.info(f"\n{'='*50}")
    logger.info(f"更新: {pkg['name']} ({key})")
    logger.info(f"{'='*50}")

    # 1. 下载
    start = time.time()
    ok = download_with_progress(zip_url, zip_path)
    if not ok:
        logger.error(f"  下载失败，跳过")
        return 0

    download_time = time.time() - start

    # 2. 解压
    extract_start = time.time()
    file_count = extract_zip(zip_path, pkg["subdir"])
    extract_time = time.time() - extract_start

    logger.info(f"  解压: {file_count} 个 .day 文件")
    logger.info(f"  下载耗时: {download_time:.1f}s")
    logger.info(f"  解压耗时: {extract_time:.1f}s")

    # 3. 验证
    target_dir = Path(TDX_DATA_PATH) / pkg["subdir"]
    actual = len(list(target_dir.glob("*.day"))) if target_dir.exists() else 0
    logger.info(f"  验证: {actual} 个文件")

    # 4. 清理 ZIP
    zip_path.unlink()

    return file_count


def main():
    parser = argparse.ArgumentParser(description="从通达信官网下载日K线数据")
    parser.add_argument("--package", help="只下载指定包（如 shlday）")
    parser.add_argument("--list", action="store_true", help="列出所有包")
    parser.add_argument("--verify", action="store_true", help="验证所有已下载数据")
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
            files = list(subdir.glob("*.day")) if subdir.exists() else []
            if not files:
                print(f"{key:15s} {pkg['subdir']:25s} {'0':>8s} {'无数据':>10s} {'':>12s} {'无数据'}")
                continue
            # 抽样验证第一条和最后一条
            sample = files[0]
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
            total_files += update_package(key, pkg, tmp_dir)
        except Exception as e:
            logger.error(f"  处理 {key} 时出错: {e}")

    # 清理临时目录
    if tmp_dir.exists() and not any(tmp_dir.iterdir()):
        tmp_dir.rmdir()

    logger.info(f"\n完成: 共更新 {total_files} 个文件")


if __name__ == "__main__":
    main()
