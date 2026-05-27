"""批量下载 A 股基本面数据到本地缓存

从 baostock（PE/PB/市值）、同花顺（财务指标）批量获取基本面数据，
缓存到 cache/fundamentals/ 目录下的 parquet 文件。

注意：东方财富 stock_individual_info_em 被封禁，改用 baostock 计算 PE。

用法：
    python scripts/fetch_fundamentals.py          # 全量下载
    python scripts/fetch_fundamentals.py --limit 10  # 测试用
    python scripts/fetch_fundamentals.py --resume    # 续传
    python scripts/fetch_fundamentals.py --force    # 强制重下
    python scripts/fetch_fundamentals.py --refresh   # 刷新失败
"""

import argparse
import logging
import os
import sys
import time
import shutil
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import signal

import pandas as pd

# 导入 FinancialCache（统一缓存层）
try:
    from lib.data_cache import get_financial_cache
except ImportError:
    get_financial_cache = None

# 强制刷新日志
def _flush_log():
    for handler in logger.handlers:
        handler.flush()
    sys.stdout.flush()

# 项目路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# akshare / baostock 缓存导入，避免重复 import
_akshare = None
_baostock = None
_baostock_lock = threading.Lock()


def _get_akshare():
    global _akshare
    if _akshare is None:
        import akshare as ak
        _akshare = ak
    return _akshare


def _get_baostock():
    global _baostock
    if _baostock is None:
        with _baostock_lock:
            if _baostock is None:  # 二次检查
                import baostock as bs
                _baostock = bs
                _baostock.login()
    return _baostock


# 尝试导入缓存模块，失败则内联
try:
    from lib.cache import retry_api_call
except ImportError:
    def retry_api_call(func, *args, max_retries=3, **kwargs):
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception:
                if attempt == max_retries - 1:
                    raise
                time.sleep(1.0 * (attempt + 1))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

CACHE_DIR = PROJECT_ROOT / "cache" / "fundamentals"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ============ 全局状态 ============
_stop_flag = False
_stop_lock = threading.Lock()


def _set_stop():
    global _stop_flag
    with _stop_lock:
        _stop_flag = True


def _is_stopped():
    with _stop_lock:
        return _stop_flag


def signal_handler(signum, frame):
    logger.warning("收到中断信号，正在停止...")
    _set_stop()


if hasattr(signal, 'SIGINT'):
    signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


# ============ 工具函数 ============

def _check_disk_space(path: Path, required_mb: int = 100) -> bool:
    """检查磁盘空间是否充足"""
    try:
        total, used, free = shutil.disk_usage(path)
        free_mb = free / (1024 * 1024)
        if free_mb < required_mb:
            logger.error(f"磁盘空间不足: 剩余 {free_mb:.0f}MB < 需要 {required_mb}MB")
            return False
        return True
    except Exception:
        return True


def _validate_parquet(path: Path, min_rows: int = 1) -> bool:
    """校验 parquet 文件是否损坏"""
    if not path.exists():
        return False
    try:
        df = pd.read_parquet(path)
        return len(df) >= min_rows
    except Exception:
        return False


def _safe_save(df: pd.DataFrame, path: Path) -> bool:
    """安全写入 parquet（先写临时文件，再验证，最后原子替换）"""
    if not _check_disk_space(path.parent, required_mb=50):
        return False

    tmp_path = path.with_suffix(".tmp.parquet")
    try:
        df.to_parquet(tmp_path, index=False)
        verify_df = pd.read_parquet(tmp_path)
        if len(verify_df) != len(df):
            raise IOError(f"验证失败：写入 {len(df)} != 读取 {len(verify_df)}")
        tmp_path.replace(path)
        return True
    except Exception as e:
        logger.error(f"保存失败: {e}")
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass
        return False


def _fetch_with_timeout(func, timeout_sec: int, desc: str, retries: int = 2) -> dict:
    """带超时 + 重试的执行包装器（不泄漏线程）

    超时或连接错误时最多重试 retries 次，每次延长等待时间。
    """
    for attempt in range(retries + 1):
        result = [None]
        exc = [None]

        # 退避延迟：首次 1s，第2次 2s...
        if attempt > 0:
            backoff = attempt * 2
            logger.warning(f"{desc} 第 {attempt} 次重试（等待 {backoff}s）...")
            time.sleep(backoff)

        def target():
            try:
                result[0] = func()
            except Exception as e:
                exc[0] = e

        t = threading.Thread(target=target)
        t.daemon = True
        t.start()
        t.join(timeout_sec)

        if t.is_alive():
            logger.warning(f"{desc} 超时 {timeout_sec}s（第 {attempt+1}/{retries+1} 次）")
            continue
        if exc[0]:
            # ConnectionError/RemoteDisconnected 时重试
            err_str = str(exc[0])
            is_conn_err = "Connection" in err_str or "RemoteDisconnected" in err_str or "aborted" in err_str
            if is_conn_err:
                logger.warning(f"{desc} 连接错误: {exc[0]}（第 {attempt+1}/{retries+1} 次）")
                continue
            logger.warning(f"{desc} 异常: {exc[0]}，已跳过")
            return {}
        return result[0] or {}

    # 全部重试失败
    logger.warning(f"{desc} 重试 {retries} 次后失败")
    return {}


# ============ 数据获取 ============

def get_stock_list() -> pd.DataFrame:
    """获取全部 A 股代码列表"""
    ak = _get_akshare()
    logger.info("获取 A 股代码列表...")
    df = retry_api_call(ak.stock_info_a_code_name, max_retries=3)
    if df.empty:
        logger.error("获取股票列表失败")
        return pd.DataFrame()
    logger.info(f"共 {len(df)} 只 A 股")
    return df


def _get_info_columns() -> dict:
    """获取需要的中文列名（带备选名映射）"""
    return {
        "市盈率-动态": ["市盈率-动态", "PE", "pe", "动态市盈率"],
        "市净率": ["市净率", "PB", "pb"],
        "总市值": ["总市值", "市值", "market_cap"],
    }


def fetch_pe_pb_baostock(code: str, timeout: int = 10) -> dict:
    """通过 baostock 获取 PE/PB/总市值

    替代被东方财富封禁的 stock_individual_info_em。
    baostock 不需要登录即可查询（login() 仅用于获取持仓等私有数据）。
    """
    def _fetch() -> dict:
        bs_lib = _get_baostock()

        # 转换代码格式: 000001 -> sz.000001, 600519 -> sh.600519
        if code.startswith(("6", "9")):
            bs_code = "sh." + code
        else:
            bs_code = "sz." + code

        result = {}

        # 1. 获取最新价格（前复权）
        try:
            rs_price = bs_lib.query_history_k_data_plus(
                code=bs_code, fields="date,close",
                start_date="2026-05-01", end_date="2026-05-18",
                frequency="d", adjustflag="2"
            )
            if rs_price.error_msg == "success":
                price_df = rs_price.get_data()
                if not price_df.empty:
                    result["price"] = float(price_df.iloc[-1]["close"])
        except Exception:
            pass

        # 2. 获取最新一期财务数据（EPS TTM、总股本、ROE）
        try:
            rs_profit = bs_lib.query_profit_data(code=bs_code, year=2025, quarter=4)
            if rs_profit.fields:
                while rs_profit.next():
                    row = rs_profit.get_row_data()
                    # row: [code, pubDate, statDate, roeAvg, npMargin, gpMargin, netProfit, epsTTM, MBRevenue, totalShare, liqaShare]
                    if row[7]:  # epsTTM
                        result["eps_ttm"] = float(row[7])
                    if row[3]:  # roeAvg
                        result["roe"] = float(row[3])
                    if row[9]:  # totalShare
                        result["total_share"] = float(row[9])
                    break
        except Exception:
            pass

        # 3. 计算 PE = price / eps_ttm
        price = result.get("price")
        eps = result.get("eps_ttm")
        if price and eps and eps > 0:
            result["pe_ttm"] = round(price / eps, 2)
        else:
            result["pe_ttm"] = None

        # 4. 计算总市值（亿元）
        total_share = result.get("total_share")
        if price and total_share:
            # totalShare 单位是股，price 单位是元，/ 1e8 = 亿元
            result["market_cap"] = round(price * total_share / 1e8, 2)
        else:
            result["market_cap"] = None

        # PB 暂不支持（需要每股净资产，baostock query_profit_data 未返回）
        result["pb"] = None

        return result

    return _fetch_with_timeout(_fetch, timeout, f"{code} PE/PB(baostock)")


def fetch_financial_indicators(code: str, timeout: int = 20) -> dict:
    """获取财务分析指标（使用同花顺 stock_financial_abstract）"""
    ak = _get_akshare()

    # 同花顺代码格式转换（股票列表返回的是纯代码如 000001）
    ths_code = code
    if code.startswith("6") or code.startswith("9"):
        ths_code = "sh" + code
    elif code.startswith(("0", "3", "2")):
        ths_code = "sz" + code

    def _fetch():
        try:
            df = ak.stock_financial_abstract(symbol=ths_code)
        except Exception:
            return {}
        if df.empty or len(df.columns) < 3:
            return {}

        # 列结构: ['选项', '指标', '20260331', '20251231', ...]
        # 验证日期列存在
        date_cols = [c for c in df.columns[2:] if str(c).isdigit()]
        if not date_cols:
            return {}

        result = {}
        # 建立指标名 → 英文名 映射
        key_map = {
            "基本每股收益": "eps",
            "稀释每股收益": "diluted_eps",
            "扣非每股收益_基本": "adjusted_eps",
            "每股净资产": "bvps",
            "净资产收益率(ROE)": "roe",
            "净资产收益率_平均": "roe_avg",
            "净资产收益率_摊薄": "roe_diluted",
            "毛利率": "gross_margin",
            "净利率": "net_margin",
            "营业利润率": "operating_margin",
            "资产负债": "debt_ratio",
            "营业总收入增长率": "revenue_growth",
            "归属母公司净利润增长率": "profit_growth",
        }
        for _, row in df.iterrows():
            indicator = str(row.iloc[1]).strip()
            if indicator in key_map:
                raw_val = row.iloc[2]
                try:
                    val = float(str(raw_val).replace("%", "").replace(",", ""))
                    # THS 返回百分比格式（34.5），转为小数格式（0.345）便于统一计算
                    if key_map[indicator] in ("roe", "roe_avg", "roe_diluted", "gross_margin", "net_margin", "operating_margin"):
                        val = val / 100.0
                    result[key_map[indicator]] = val
                except (ValueError, TypeError):
                    result[key_map[indicator]] = None
        return result

    return _fetch_with_timeout(_fetch, timeout, f"{code} 财务指标")


_em_debt_ratio_cache = {}  # code -> debt_ratio，批量预热时复用


def _fetch_em_debt_ratio_batch(code: str) -> None:
    """从 East Money 批量获取 debt_ratio（写入模块级缓存，避免重复请求）"""
    if code in _em_debt_ratio_cache:
        return
    try:
        from lib.data_eastmoney_fin import get_debt_ratio as _get_em_dr
        dr = _get_em_dr(code)
        _em_debt_ratio_cache[code] = dr
    except Exception:
        _em_debt_ratio_cache[code] = None


def _to_financial_cache_format(record: dict) -> dict:
    """将 THS/baostock 记录转换为 FinancialCache 标准格式

    FinancialCache 统一格式:
        {
            "profitability": {ROE, gross_margin, net_margin, operating_margin, ebitda_margin},
            "solvency": {debt_to_asset, debt_to_equity, equity_multiplier, interest_coverage},
            "growth": {revenue_growth, net_income_growth},
            "_source": "thshare",
        }
    """
    prof = {}
    sol = {}
    growth = {}

    roe = record.get("roe")
    if roe is not None:
        prof["ROE"] = roe

    gm = record.get("gross_margin")
    if gm is not None:
        prof["gross_margin"] = gm

    nm = record.get("net_margin")
    if nm is not None:
        prof["net_margin"] = nm

    om = record.get("operating_margin")
    if om is not None:
        prof["operating_margin"] = om

    dr = record.get("debt_ratio")
    if dr is not None:
        sol["debt_to_asset"] = dr

    rg = record.get("revenue_growth")
    if rg is not None:
        growth["revenue_growth"] = rg

    pg = record.get("profit_growth")
    if pg is not None:
        growth["net_income_growth"] = pg

    return {
        "profitability": prof,
        "solvency": sol,
        "growth": growth,
        "_source": "thshare",
    }


def _prewarm_cache(code: str, record: dict) -> None:
    """将单条记录写入 FinancialCache（如可用）"""
    if get_financial_cache is None:
        return
    try:
        # 先从 East Money 补充 debt_ratio
        _fetch_em_debt_ratio_batch(code)
        em_dr = _em_debt_ratio_cache.get(code)
        if em_dr is not None and record.get("debt_ratio") is None:
            record = dict(record)  # 浅拷贝，避免修改原 record
            record["debt_ratio"] = em_dr

        cache = get_financial_cache()
        formatted = _to_financial_cache_format(record)
        cache.set(code, formatted)
    except Exception:
        pass  # 预热失败不影响主流程


def fetch_single_stock(code: str, name: str = "") -> dict:
    """获取单只股票的完整基本面数据"""
    if _is_stopped():
        raise KeyboardInterrupt("用户中断")

    result = {"code": code, "name": name, "fetch_time": datetime.now().isoformat()}

    # PE/PB/总市值：通过 baostock 计算（东方财富 API 被封禁）
    pe_data = fetch_pe_pb_baostock(code, timeout=15)
    result.update({
        "pe_ttm": pe_data.get("pe_ttm"),
        "market_cap": pe_data.get("market_cap"),
    })

    # THS 财务指标（ROE/毛利率/eps/bvps/负债率等）
    fin = fetch_financial_indicators(code, timeout=20)
    result.update(fin)

    # 用 price / bvps 计算 PB（baostock 不直接返回 PB）
    price = pe_data.get("price")
    bvps = fin.get("bvps")
    if price and bvps and bvps > 0:
        result["pb"] = round(price / bvps, 2)
    else:
        result["pb"] = None
    result["price"] = price  # 用于调试

    # baostock 的 ROE 覆盖 THS 的 ROE（通常更准）
    if pe_data.get("roe") is not None:
        result["roe"] = pe_data["roe"]

    # 预热 FinancialCache（异步写入，不阻塞主流程）
    _prewarm_cache(code, result)

    return result


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(description="批量下载 A 股基本面数据")
    parser.add_argument("--limit", type=int, default=0, help="限制下载数量")
    parser.add_argument("--resume", action="store_true", help="续传（跳过已有）")
    parser.add_argument("--force", action="store_true", help="强制重下所有")
    parser.add_argument("--refresh", action="store_true", help="刷新缺失数据（重新下载PE/PB为空的股票）")
    parser.add_argument("--workers", type=int, default=3, help="并发数")
    parser.add_argument("--retry", type=int, default=2, help="失败自动重试次数")
    args = parser.parse_args()

    cache_path = CACHE_DIR / "fundamentals.parquet"

    stock_df = get_stock_list()
    if stock_df.empty:
        return

    codes = stock_df["code"].tolist()
    names = dict(zip(stock_df["code"], stock_df["name"]))

    if args.limit > 0:
        codes = codes[:args.limit]

    # 加载已有缓存（用 dict 便于实时去重）
    existing = {}  # code -> record
    existing_codes = set()

    if args.resume and cache_path.exists():
        if _validate_parquet(cache_path):
            try:
                existing_df = pd.read_parquet(cache_path)
                for _, row in existing_df.iterrows():
                    code = str(row.get("code", ""))
                    existing[code] = row.to_dict()
                existing_codes = set(existing.keys())
                logger.info(f"已有缓存 {len(existing)} 只")

                # 刷新模式：找出 PE/PB 为空的股票，重新下载
                if args.refresh:
                    missing_codes = [
                        code for code, data in existing.items()
                        if not data.get("pe_ttm") and not data.get("pb")
                    ]
                    logger.info(f"刷新模式：{len(missing_codes)} 只股票 PE/PB 为空，将重新下载")
                    for code in missing_codes:
                        del existing[code]
                    existing_codes -= set(missing_codes)

            except Exception as e:
                logger.warning(f"读取缓存失败: {e}")
                existing = {}
        else:
            logger.warning("缓存文件损坏，将重新下载")

    # 待下载列表
    if args.force:
        remaining_codes = codes[:]
    else:
        remaining_codes = [c for c in codes if c not in existing_codes]

    total_to_download = len(remaining_codes)
    logger.info(f"待下载: {total_to_download} 只（并发{args.workers}）")

    t0 = time.time()
    failed = []
    # 用 dict 代替 list，避免重复去重操作
    all_records_dict = dict(existing)
    downloaded = len(existing)

    if not _check_disk_space(cache_path.parent):
        logger.error("磁盘空间不足，退出")
        return

    def fetch_one(code):
        name = names.get(code, "")
        try:
            return code, fetch_single_stock(code, name), None
        except KeyboardInterrupt:
            raise
        except Exception as e:
            return code, None, str(e)

    MAX_RETRIES = max(0, args.retry)
    pending_codes = remaining_codes[:]
    completed = 0
    shutdown_requested = False

    for attempt in range(MAX_RETRIES + 1):
        if _is_stopped():
            break

        if attempt > 0:
            if failed:
                logger.info(f"第 {attempt} 次重试，{len(failed)} 只失败股票，等待 10s 让 API 冷却...")
                time.sleep(10)  # 整批重试前让 API 冷却
                pending_codes = failed[:]
                failed = []
            else:
                break

        # 重试时重置计数器
        if attempt > 0:
            completed = 0

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(fetch_one, code): code for code in pending_codes}
            shutdown_requested = False

            for future in as_completed(futures):
                if _is_stopped() and not shutdown_requested:
                    executor.shutdown(wait=False)
                    shutdown_requested = True

                # 检查 future 是否被拒绝（shutdown 后可能拒绝）
                try:
                    code, record, err = future.result(timeout=1.0)
                except Exception:
                    continue

                completed += 1

                if record:
                    all_records_dict[code] = record
                    downloaded += 1
                else:
                    failed.append(code)
                    if err and completed % 100 == 0:
                        logger.warning(f"{code} 失败: {err}")

                if completed % 100 == 0 or completed == len(pending_codes):
                    elapsed = time.time() - t0
                    rate = completed / elapsed if elapsed > 0 else 0
                    logger.info(
                        f"[{completed}/{len(pending_codes)}] "
                        f"下载 {downloaded}, 失败 {len(failed)}, "
                        f"速度 {rate:.1f} 只/秒"
                    )
                    _flush_log()

                # 每 100 只保存一次（dict 已去重，直接转 df 保存）
                if completed % 100 == 0 and all_records_dict:
                    df = pd.DataFrame(list(all_records_dict.values()))
                    logger.info(f"保存进度: {len(df)} 条...")
                    _flush_log()
                    if _safe_save(df, cache_path):
                        logger.info(f"已保存 {len(df)} 条")
                        _flush_log()
                    # 每 100 只后稍作休息，降低东方财富限流触发概率
                    time.sleep(1)

    # 最终保存
    if all_records_dict:
        df = pd.DataFrame(list(all_records_dict.values()))
        _safe_save(df, cache_path)
        elapsed = time.time() - t0
        logger.info(
            f"完成! 共 {len(df)} 只, "
            f"耗时 {elapsed:.0f}s ({len(df)/elapsed:.1f} 只/秒)"
        )

        # 打印统计
        print(f"\n{'='*50}")
        print(f"  基本面数据下载完成")
        print(f"{'='*50}")
        print(f"  股票总数: {len(df)}")
        for col in ["pe_ttm", "pb", "roe", "debt_ratio", "gross_margin"]:
            if col in df.columns:
                valid = df[col].notna().sum()
                mean_val = df[col].mean()
                print(f"  {col:15s}: {valid:>5d} 只有值, 均值={mean_val:.2f}")
        if failed:
            print(f"  失败: {len(failed)} 只")
            print(f"  失败代码: {failed[:10]}{'...' if len(failed) > 10 else ''}")
        print(f"  缓存路径: {cache_path}")
        print(f"{'='*50}")
    else:
        logger.warning("无数据")


if __name__ == "__main__":
    main()
