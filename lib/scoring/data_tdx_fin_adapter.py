from lib.data.data_utils import safe_div

from lib.data.data_utils import to_float

"""TDX 财务数据适配器

将 gpcw*.dat 的原始字段映射到 compute_fundamentals() 需要的格式。

字段映射（已验证）：
  field 0:  净利润（万元单位）
  field 2:  营业总收入（万元）
  field 3:  营业收入（万元）
  field 5:  利润总额（万元）
  field 12: 所得税（万元）— 验证：field_5 - field_12 ≈ field_0
  field 50: 每股收益(摊薄)，原始值 70 ≈ 65-70元（实际EPS），单位=元
  field 51: 每股净资产，原始值 17847 ≈ 178.47元，单位=分
  field 75: 资产总计（万元）
  field 76: 负债合计（万元）

注意：field_4 的值 > field_3，映射为"营业利润"存疑，可能是营业总成本。

增长率计算：需要多期数据对比（见 get_financial_data_multi_period）
"""
from datetime import datetime
from pathlib import Path
import logging
import pandas as pd
from typing import Dict, Optional, List
from lib.data.data_tdx_fin import GpcwReader
from lib.data.data_cache import get_financial_cache

logger = logging.getLogger(__name__)

# gpcw 字段到 compute_fundamentals 输入的映射
# 格式: gpcw_field_index: (target_key, multiplier)
# 单位转换：金额字段原始值单位=万元，乘以10000转为元
# 每股指标原始值单位=分，需除以100转为元
GPCW_TO_INCOME = {
    0: ("net_income", 10000),         # 净利润
    2: ("revenue", 10000),            # 营业总收入
    3: ("operating_income", 10000),   # 营业收入
    5: ("profit_total", 10000),       # 利润总额
    12: ("income_tax", 10000),        # 所得税
}

GPCW_TO_BALANCE = {
    75: ("total_assets", 10000),      # 资产总计
    76: ("total_liabilities", 10000), # 负债合计
}

GPCW_TO_PER_SHARE = {
    50: ("eps", 1.0),    # 每股收益(摊薄)，field_50=70≈65-70元（实际EPS），单位=元
    51: ("bvps", 0.01),  # 每股净资产，field_51=17847≈178.47元（实际BVPS），单位=分，需除100
}


def _safe_div(num, den):
    """安全除法"""
    return safe_div(num, den)


class TdxFinAdapter:
    """TDX 财务数据适配器

    将 gpcw 原始字段转换为 compute_fundamentals() 所需格式
    """

    def __init__(self):
        self._reader = GpcwReader()

    def _raw_to_dict(self, df: pd.DataFrame) -> Dict[int, float]:
        """将 DataFrame 转为 field_index -> value_main 的 dict"""
        if df.empty:
            return {}
        return {int(row["field_index"]): row["value_main"] for _, row in df.iterrows()}

    def get_raw_data(self, code: str, filepath: Optional[Path] = None) -> pd.DataFrame:
        """获取原始财务数据

        Args:
            code: 6位股票代码
            filepath: 可选，指定 gpcw 文件路径
        """
        return self._reader.read_stock(code, filepath=filepath)

    def get_financial_data(self, code: str, period: Optional[str] = None) -> Optional[Dict]:
        """获取财务数据（转换后的格式）

        Args:
            code: 6位股票代码
            period: 可选，指定时期（如 "20251231" 或 "20260331"）

        Returns:
            Dict with keys: income, balance, per_share
        """
        filepath = None
        if period:
            all_files = self._reader.get_all_period_files()
            for f in all_files:
                if f.stem[4:] == period:
                    filepath = f
                    break

        df = self._reader.read_stock(code, filepath=filepath)
        if df.empty:
            return None

        field_data = self._raw_to_dict(df)

        result = {
            "income": {},
            "balance": {},
            "per_share": {},
        }

        for field_idx, (key, multiplier) in GPCW_TO_INCOME.items():
            if field_idx in field_data:
                result["income"][key] = field_data[field_idx] * multiplier

        for field_idx, (key, multiplier) in GPCW_TO_BALANCE.items():
            if field_idx in field_data:
                result["balance"][key] = field_data[field_idx] * multiplier

        for field_idx, (key, multiplier) in GPCW_TO_PER_SHARE.items():
            if field_idx in field_data:
                result["per_share"][key] = field_data[field_idx] * multiplier

        return result

    def get_financial_data_multi_period(self, code: str, periods: Optional[List[str]] = None) -> Dict[str, Dict]:
        """获取多期财务数据（用于计算增长率）

        Args:
            code: 6位股票代码
            periods: 可选，要获取的时期列表（YYYYMMDD），默认获取所有可用时期

        Returns:
            Dict {period: financial_data}
        """
        all_files = self._reader.get_all_period_files()

        if periods is None:
            periods = [f.stem[4:] for f in all_files if f.stat().st_size > 0]

        result = {}
        for period in periods:
            for f in all_files:
                if f.stem[4:] == period:
                    data = self.get_financial_data(code, period=period)
                    if data:
                        result[period] = data
                    break
        return result

    def compute_fundamentals(self, code: str, period: Optional[str] = None) -> Optional[Dict]:
        """获取计算后的财务指标

        Args:
            code: 6位股票代码
            period: 可选，指定时期（默认最新）

        Returns:
            Dict with profitability/solvency/liquidity/etc. (see compute_fundamentals)
        """
        from lib.scoring.fundamental import compute_fundamentals

        data = self.get_financial_data(code, period=period)
        if data is None:
            return None

        income = dict(data.get("income", {}))
        balance = dict(data.get("balance", {}))
        cashflow = data.get("cashflow", {})
        tdx_per_share = data.get("per_share", {})

        # 处理 shareholders_equity
        if "shareholders_equity" not in balance or balance["shareholders_equity"] is None:
            ta = balance.get("total_assets")
            tl = balance.get("total_liabilities")
            if ta is not None and tl is not None:
                balance["shareholders_equity"] = ta - tl

        result = compute_fundamentals(
            income=income,
            balance=balance,
            cashflow=cashflow,
        )

        # 合并 TDX 直接的每股数据（EPS/BVPS）
        if tdx_per_share:
            if "per_share" not in result:
                result["per_share"] = {}
            for k, v in tdx_per_share.items():
                if v is not None:
                    result["per_share"][k.upper()] = v

        # 补充增长率（多期数据对比，只比较相邻的同类期间）
        try:
            all_periods = self.get_financial_data_multi_period(code)
            if len(all_periods) >= 2:
                # 按日期排序（最新的在前）
                sorted_periods = sorted(all_periods.keys(), reverse=True)

                # 尝试找到最近的前一个"同期"期间（同月份，用于同比）
                # 期间格式: YYYYMMdd，月份在位置 4-6
                def get_period_type(p):
                    """返回 (年份, 月份) 元组，用于判断是否同类型报告"""
                    return (p[:4], p[4:6])

                curr_type = get_period_type(sorted_periods[0])
                prior_period = None
                for p in sorted_periods[1:]:
                    if get_period_type(p) == curr_type:
                        prior_period = p
                        break

                # 如果没找到同期（月份相同），用最近的一期
                if prior_period is None and len(sorted_periods) > 1:
                    prior_period = sorted_periods[1]

                if prior_period:
                    current_data = all_periods[sorted_periods[0]]
                    prior_data = all_periods[prior_period]

                    curr_ni = current_data.get("income", {}).get("net_income")
                    prior_ni = prior_data.get("income", {}).get("net_income")
                    curr_rev = current_data.get("income", {}).get("revenue")
                    prior_rev = prior_data.get("income", {}).get("revenue")

                    if "growth" not in result:
                        result["growth"] = {}
                    result["growth"]["net_income_growth"] = _safe_div(
                        None if curr_ni is None or prior_ni is None else curr_ni - prior_ni,
                        prior_ni
                    )
                    result["growth"]["revenue_growth"] = _safe_div(
                        None if curr_rev is None or prior_rev is None else curr_rev - prior_rev,
                        prior_rev
                    )
        except Exception as e:
            logger.debug(f"[tdx_fin_adapter] 增长率计算失败 [{code}]: {e}")

        return result


# 全局实例
_tdx_fin_adapter: Optional[TdxFinAdapter] = None


def get_tdx_fin_adapter() -> TdxFinAdapter:
    global _tdx_fin_adapter
    if _tdx_fin_adapter is None:
        _tdx_fin_adapter = TdxFinAdapter()
    return _tdx_fin_adapter


def _to_float(v) -> Optional[float]:
    """将字符串转为 float，处理 None/空值"""
    return to_float(v, strip_grouping=True, dash_is_none=True)


_bs_session = None  # type: ignore
_bs_last_used: float = 0


def _get_bs():
    """获取或创建 baostock session（避免重复 login，30 分钟超时重连）"""
    global _bs_session, _bs_last_used
    import time
    now = time.time()

    # Session unused for 30+ minutes → reconnect
    if _bs_session is not None and (now - _bs_last_used) > 1800:
        try:
            _bs_session.logout()
        except Exception:
            pass
        _bs_session = None

    if _bs_session is None:
        import baostock as bs
        lg = bs.login()
        if lg.error_code == "0":
            _bs_session = bs
    _bs_last_used = now
    return _bs_session


def _fetch_baostock_fundamentals(code: str) -> Optional[Dict]:
    """从 baostock 获取财务指标（当 TDX gpcw 无数据时的 fallback）"""
    try:
        bs = _get_bs()
        if bs is None:
            return None

        clean = code.replace("sh.", "").replace("sz.", "")
        bs_code = f"sh.{clean}" if clean.startswith("6") else f"sz.{clean}"

        current_year = datetime.now().year
        result = None

        # 从本年到 2020 年逐 year 搜索，找到即停
        for year in range(current_year, 2019, -1):
            # ── dupont_data: ROE ──
            dupont_roe = None
            asset_to_equity = None
            try:
                rs_dup = bs.query_dupont_data(bs_code, year=year, quarter=4)
                if rs_dup.error_code == "0":
                    while rs_dup.next():
                        row = rs_dup.get_row_data()
                        dupont_roe = _to_float(row[3])
                        asset_to_equity = _to_float(row[4])
                        break
            except Exception as e:
                logger.debug(f"[tdx_fin_adapter] baostock dupont 查询失败 [{code}] year={year}: {e}")

            # ── profit_data ──
            net_margin = None
            gross_margin = None
            net_income = None
            revenue = None
            roe_avg = None
            try:
                rs_profit = bs.query_profit_data(bs_code, year=year, quarter=4)
                if rs_profit.error_code == "0":
                    while rs_profit.next():
                        row = rs_profit.get_row_data()
                        roe_avg = _to_float(row[3])
                        net_margin = _to_float(row[4])
                        gross_margin = _to_float(row[5])
                        net_income = _to_float(row[6])
                        revenue = _to_float(row[8])
                        break
            except Exception as e:
                logger.debug(f"[tdx_fin_adapter] baostock profit 查询失败 [{code}] year={year}: {e}")

            # 只要有 ROE 数据就算成功
            if dupont_roe is None and roe_avg is None:
                continue

            # ── growth_data ──
            revenue_growth = None
            net_income_growth = None
            try:
                rs_growth = bs.query_growth_data(bs_code, year=year, quarter=4)
                if rs_growth.error_code == "0":
                    while rs_growth.next():
                        row = rs_growth.get_row_data()
                        revenue_growth = _to_float(row[4])
                        net_income_growth = _to_float(row[5])
                        break
            except Exception as e:
                logger.debug(f"[tdx_fin_adapter] baostock growth 查询失败 [{code}] year={year}: {e}")

            # ── balance_data ──
            total_assets = None
            total_liabilities = None
            try:
                rs_bal = bs.query_balance_data(bs_code, year=year, quarter=4)
                if rs_bal.error_code == "0":
                    while rs_bal.next():
                        row = rs_bal.get_row_data()
                        total_assets = _to_float(row[3])
                        total_liabilities = _to_float(row[4])
                        break
            except Exception as e:
                logger.debug(f"[tdx_fin_adapter] baostock balance 查询失败 [{code}] year={year}: {e}")

            roe = dupont_roe if dupont_roe is not None else roe_avg
            debt_to_asset = None
            if total_assets and total_liabilities and total_assets > 0:
                debt_to_asset = total_liabilities / total_assets

            result = {
                "profitability": {
                    "ROE": roe,
                    "ROA": None,
                    "gross_margin": gross_margin,
                    "net_margin": net_margin,
                    "operating_margin": None,
                    "ebitda_margin": None,
                },
                "solvency": {
                    "debt_to_asset": debt_to_asset,
                    "debt_to_equity": asset_to_equity,
                    "equity_multiplier": None,
                    "interest_coverage": None,
                },
                "growth": {
                    "revenue_growth": revenue_growth,
                    "net_income_growth": net_income_growth,
                },
                "_source": "baostock",
            }
            break

        return result
    except Exception as e:
        logger.debug(f"[tdx_fin_adapter] baostock 财务数据获取失败 [{code}]: {e}")
        return None


def _tdx_fin_available() -> bool:
    """复用 data_tdx_fin 的单一可用性判据（P-104）。"""
    from lib.data.data_tdx_fin import tdx_fin_available
    return tdx_fin_available()


def get_stock_fundamentals(code: str, period: Optional[str] = None) -> Optional[Dict]:
    """获取单只股票的完整财务指标（优先 TDX gpcw，TDX 无数据或 ROE 明显异常时 fallback 到 baostock）"""
    # 0. 项目内本地数据仓（ADR-0033）：离线只读；在线时新鲜数据直接复用
    from lib.data import local_store
    if local_store.enabled():
        stored = local_store.read_json("financial", code)
        if stored is not None and (local_store.offline() or local_store.is_fresh("financial", code)):
            return stored
        if local_store.offline():
            return None

    # 1. 缓存检查
    cache = get_financial_cache()
    cached = cache.get(code)
    if cached is not None:
        local_store.write_json("financial", code, cached)
        return cached

    # 2. TDX 本地数据（目录不存在时直接走兜底，不逐股触发 "找不到 gpcw" 告警）
    result = None
    if _tdx_fin_available():
        adapter = get_tdx_fin_adapter()
        result = adapter.compute_fundamentals(code, period=period)
    else:
        logger.debug("[tdx_fin_adapter] 本地 gpcw 数据不可用，直接走 baostock 兜底")
    if result is not None:
        roe = result.get("profitability", {}).get("ROE")
        if roe is not None and -0.1 < roe < 0.5:
            # 从 East Money 补充 debt_ratio（如 TDX 没有）
            solv = result.get("solvency", {})
            if solv.get("debt_to_asset") is None:
                try:
                    from lib.data.data_eastmoney_fin import get_debt_ratio as _em_debt_ratio
                    em_dr = _em_debt_ratio(code)
                    if em_dr is not None:
                        solv["debt_to_asset"] = em_dr
                except Exception as e:
                    logger.debug(f"[tdx_fin_adapter] East Money 负债率获取失败 [{code}]: {e}")

            # 从 East Money 补充增长率（如 TDX 没有）
            growth = result.get("growth", {})
            try:
                from lib.data.data_eastmoney_fin import get_em_financial as _em_fin
                em_data = _em_fin(code)
                if em_data:
                    if growth.get("revenue_growth") is None and em_data.get("revenue_growth") is not None:
                        growth["revenue_growth"] = em_data["revenue_growth"]
                    if growth.get("net_income_growth") is None and em_data.get("net_income_growth") is not None:
                        growth["net_income_growth"] = em_data["net_income_growth"]
            except Exception as e:
                logger.debug(f"[tdx_fin_adapter] East Money 增长率补充失败 [{code}]: {e}")

            result["_source"] = "tdx"
            cache.set(code, result)
            local_store.write_json("financial", code, result)
            return result

    # 3. TDX 无数据或 ROE 异常，fallback 到 baostock
    bs_result = _fetch_baostock_fundamentals(code)
    if bs_result is not None:
        # 从 East Money 补充 debt_ratio 和增长率（如 baostock 没有）
        solv = bs_result.get("solvency", {})
        growth = bs_result.get("growth", {})
        try:
            from lib.data.data_eastmoney_fin import get_em_financial as _em_fin
            em_data = _em_fin(code)
            if em_data:
                if solv.get("debt_to_asset") is None and em_data.get("debt_ratio") is not None:
                    solv["debt_to_asset"] = em_data["debt_ratio"]
                if growth.get("revenue_growth") is None and em_data.get("revenue_growth") is not None:
                    growth["revenue_growth"] = em_data["revenue_growth"]
                if growth.get("net_income_growth") is None and em_data.get("net_income_growth") is not None:
                    growth["net_income_growth"] = em_data["net_income_growth"]
        except Exception as e:
            logger.debug(f"[tdx_fin_adapter] baostock East Money 补充失败 [{code}]: {e}")

        bs_result["_source"] = "baostock"
        cache.set(code, bs_result)
        local_store.write_json("financial", code, bs_result)
        return bs_result

    # 4. 东财一等兜底源（ADR-0035）：TDX(删) + baostock(可能拉黑) 都失败时接管。
    #    用新增的 get_em_financial_full（完整标准形态），不改 get_em_financial 的 7 字段契约。
    try:
        from lib.data.data_eastmoney_fin import get_em_financial_full as _em_full
        em_result = _em_full(code)
        if em_result is not None:
            cache.set(code, em_result)
            local_store.write_json("financial", code, em_result)
            return em_result
    except Exception as e:
        logger.debug(f"[tdx_fin_adapter] 东财完整财务兜底失败 [{code}]: {e}")
    return None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    adapter = TdxFinAdapter()

    # 列出所有可用时期
    all_files = adapter._reader.get_all_period_files()
    print("可用时期:")
    for f in all_files:
        size = f.stat().st_size
        print(f"  {f.stem[4:]}: {size/1e6:.1f}MB")

    for code, name in [("600519", "贵州茅台"), ("600036", "招商银行")]:
        print(f"\n{'='*50}")
        print(f"{code} {name}")
        print(f"{'='*50}")

        # 多期数据
        multi = adapter.get_financial_data_multi_period(code)
        print(f"可用期数: {len(multi)}")
        for p, d in sorted(multi.items()):
            ni = d.get("income", {}).get("net_income")
            rev = d.get("income", {}).get("revenue")
            ni_str = f"{ni/1e8:.2f}亿" if ni is not None else "N/A"
            rev_str = f"{rev/1e8:.2f}亿" if rev is not None else "N/A"
            print(f"  {p}: 净利润={ni_str}, 营收={rev_str}")

        fin = adapter.compute_fundamentals(code)
        if fin:
            print(f"\n盈利能力:")
            for k, v in fin.get("profitability", {}).items():
                if v is not None:
                    print(f"  {k}: {v:.4f}")

            print(f"\n每股指标:")
            for k, v in fin.get("per_share", {}).items():
                if v is not None:
                    print(f"  {k}: {v:.4f}")

            if fin.get("growth"):
                print(f"\n增长指标:")
                for k, v in fin["growth"].items():
                    if v is not None:
                        print(f"  {k}: {v:.4f} ({v*100:.1f}%)")
        else:
            print("无数据")
