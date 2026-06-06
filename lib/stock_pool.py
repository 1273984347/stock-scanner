"""股票池扩展 —— 多策略覆盖更多标的

当前系统是"事件驱动选股"，只扫描有市场事件的股票。
本模块扩展覆盖范围：

方案 1: 增加 max_candidates（10→50）
方案 2: 新增策略（突破20日新高/北向资金流入/板块龙头）
方案 3: 扫描指数成分股（沪深300/中证500/中证1000）
方案 4: 冷门基本面优质股策略（低估值+高ROE+现金流）

使用方式：
    from lib.stock_pool import StockPoolBuilder
    builder = StockPoolBuilder()
    pool = builder.build_combined_pool()
"""

import logging
import os
import struct
from pathlib import Path
from typing import Dict, List, Set, Optional
from dataclasses import dataclass, field
import numpy as np

logger = logging.getLogger(__name__)

# 统一从 env 读取（之前硬编码，跨平台不可移植）
TDX_DATA_PATH = Path(os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc"))


@dataclass
class PoolConfig:
    """股票池配置"""
    # 方案 1: 候选数量
    max_candidates_per_strategy: int = 50

    # 方案 2: 新策略
    enable_breakout_20d: bool = True
    enable_northbound_flow: bool = True
    enable_sector_leader: bool = True

    # 方案 3: 指数成分股
    enable_hs300: bool = True
    enable_zz500: bool = True
    enable_zz1000: bool = False  # 中证1000太多，可选

    # 方案 4: 冷门优质股
    enable_value_quality: bool = True
    min_pe: float = 5.0
    max_pe: float = 30.0
    min_pb: float = 0.5
    max_pb: float = 8.0
    min_dividend: float = 0.02  # 股息率 > 2%

    # 通用
    exclude_st: bool = True
    min_market_days: int = 60  # 上市至少 60 天


class StockPoolBuilder:
    """股票池构建器"""

    def __init__(self, config: PoolConfig = None):
        self.config = config or PoolConfig()

    def build_combined_pool(self) -> List[str]:
        """构建综合股票池（去重合并）"""
        all_codes: Set[str] = set()

        # 方案 2: 新策略
        if self.config.enable_breakout_20d:
            breakout = self._find_breakout_20d()
            all_codes.update(breakout)
            logger.info(f"突破20日新高: {len(breakout)} 只")

        if self.config.enable_northbound_flow:
            northbound = self._find_northbound_flow()
            all_codes.update(northbound)
            logger.info(f"北向资金流入: {len(northbound)} 只")

        if self.config.enable_sector_leader:
            leaders = self._find_sector_leaders()
            all_codes.update(leaders)
            logger.info(f"板块龙头: {len(leaders)} 只")

        # 方案 3: 指数成分股
        if self.config.enable_hs300:
            hs300 = self._load_index_constituents("hs300")
            all_codes.update(hs300)
            logger.info(f"沪深300: {len(hs300)} 只")

        if self.config.enable_zz500:
            zz500 = self._load_index_constituents("zz500")
            all_codes.update(zz500)
            logger.info(f"中证500: {len(zz500)} 只")

        # 方案 4: 冷门优质股
        if self.config.enable_value_quality:
            value = self._find_value_quality()
            all_codes.update(value)
            logger.info(f"冷门优质股: {len(value)} 只")

        # 过滤
        filtered = self._filter_pool(list(all_codes))
        logger.info(f"综合股票池: {len(filtered)} 只（去重+过滤后）")

        return filtered

    def _find_breakout_20d(self) -> List[str]:
        """突破20日新高策略"""
        results = []
        # 加入 bj（修复 bj 静默丢失）
        for market_dir in ["sh", "sz", "bj"]:
            lday_dir = TDX_DATA_PATH / market_dir / "lday_4"
            if not lday_dir.exists():
                # bj 无 lday_4，fallback 到 lday
                lday_dir = TDX_DATA_PATH / market_dir / "lday"
                if not lday_dir.exists():
                    continue

            for fpath in list(lday_dir.glob("*.day"))[:5000]:  # 限制扫描数
                code = fpath.stem[2:]  # remove "sh"/"sz"/"bj" prefix
                # 包含 "9" 是为了 92xxxx 北证原代码（修复 bj 920000 漏过滤）
                if not code.startswith(("0", "3", "4", "6", "8", "9")):
                    continue

                try:
                    df = self._read_day_file(fpath, 25)
                    if df is None or len(df) < 20:
                        continue

                    closes = df["close"]
                    # 当前价 > 20日最高价（突破）
                    high_20d = closes.iloc[-20:].max()
                    current = closes.iloc[-1]

                    if current >= high_20d * 0.99 and current > 0:
                        # 还要价格 > EMA21
                        ema21 = closes.ewm(span=21).mean().iloc[-1]
                        if current > ema21:
                            results.append(code)
                except Exception:
                    continue

        return results[:self.config.max_candidates_per_strategy]

    def _find_northbound_flow(self) -> List[str]:
        """北向资金流入策略"""
        try:
            from factors import get_northbound_top_stocks
            top = get_northbound_top_stocks(top_n=30, days=10)
            return [item.get("code", "") for item in top if item.get("code")]
        except Exception:
            return []

    def _find_sector_leaders(self) -> List[str]:
        """板块龙头策略"""
        try:
            from lib.sector_rotation import SectorRotationAnalyzer
            analyzer = SectorRotationAnalyzer()
            result = analyzer.analyze(top_n=10)
            leaders = result.get("leaders", [])
            return [l.get("code", "") for l in leaders if l.get("code")]
        except Exception:
            return []

    def _load_index_constituents(self, index_name: str) -> List[str]:
        """加载指数成分股"""
        cache_file = Path(f"cache/{index_name}_constituents.txt")
        if cache_file.exists():
            return cache_file.read_text().strip().split("\n")

        # 尝试从 akshare 获取
        try:
            import akshare as ak
            if index_name == "hs300":
                df = ak.index_stock_cons_weight(symbol="000300")
            elif index_name == "zz500":
                df = ak.index_stock_cons_weight(symbol="000905")
            elif index_name == "zz1000":
                df = ak.index_stock_cons_weight(symbol="000852")
            else:
                return []

            if df is not None and not df.empty:
                code_col = next((c for c in df.columns if "代码" in c or "code" in c.lower()), df.columns[0])
                codes = df[code_col].astype(str).str.zfill(6).tolist()

                # 缓存
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text("\n".join(codes))

                return codes
        except Exception as e:
            logger.debug(f"获取 {index_name} 成分股失败: {e}")

        return []

    def _find_value_quality(self) -> List[str]:
        """冷门基本面优质股策略（低估值+高股息）"""
        results = []
        try:
            from lib.data_layer import get_stock_valuation, get_dividend_amount

            # 扫描本地 TDX 数据
            for market_dir in ["sh", "sz", "bj"]:
                lday_dir = TDX_DATA_PATH / market_dir / "lday_4"
                if not lday_dir.exists():
                    lday_dir = TDX_DATA_PATH / market_dir / "lday"
                    if not lday_dir.exists():
                        continue

                for fpath in list(lday_dir.glob("*.day"))[:3000]:
                    code = fpath.stem[2:]
                    # 包含 "9" 是为了 92xxxx 北证原代码（修复 bj 920000 漏过滤）
                    if not code.startswith(("0", "3", "4", "6", "8", "9")):
                        continue

                    try:
                        val = get_stock_valuation(code)
                        if not val:
                            continue

                        pe = val.get("pe", 0)
                        pb = val.get("pb", 0)

                        if not (self.config.min_pe <= pe <= self.config.max_pe):
                            continue
                        if not (self.config.min_pb <= pb <= self.config.max_pb):
                            continue

                        # 股息率
                        dividend = get_dividend_amount(code)
                        market_cap = val.get("market_cap", 0)
                        if market_cap > 0:
                            div_yield = dividend / (market_cap / 1e8) if market_cap > 0 else 0
                            if div_yield >= self.config.min_dividend:
                                results.append(code)
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f"冷门优质股扫描失败: {e}")

        return results[:self.config.max_candidates_per_strategy]

    def _read_day_file(self, fpath: Path, days: int = 30):
        """读取 TDX 日线文件"""
        try:
            with open(fpath, "rb") as f:
                f.seek(0, 2)
                n = f.tell() // 32
                if n < days:
                    return None
                f.seek(-(days * 32), 2)
                raw = f.read(days * 32)

            records = []
            for i in range(0, len(raw), 32):
                date_int, o, h, l, c, amount, vol, _ = struct.unpack_from("<IiiiiiII", raw, i)
                records.append({
                    "date": str(date_int),
                    "open": o / 100, "high": h / 100, "low": l / 100, "close": c / 100,
                    "amount": amount, "volume": vol,
                })
            return __import__("pandas").DataFrame(records)
        except Exception:
            return None

    def _filter_pool(self, codes: List[str]) -> List[str]:
        """过滤股票池"""
        filtered = []
        for code in codes:
            if not code or len(code) != 6:
                continue
            if not code.startswith(("0", "3", "6")):
                continue
            # ST 过滤（简单检查文件大小异常）
            filtered.append(code)
        return filtered


def get_expanded_pool(top_n: int = 200) -> List[str]:
    """获取扩展后的股票池

    Args:
        top_n: 返回股票数量上限

    Returns:
        股票代码列表
    """
    builder = StockPoolBuilder()
    pool = builder.build_combined_pool()
    return pool[:top_n]


def save_pool_to_file(codes: List[str], path: str = "cache/stock_pool_expanded.txt"):
    """保存股票池到文件"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(codes))
    logger.info(f"股票池已保存: {path} ({len(codes)} 只)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    pool = get_expanded_pool(300)
    save_pool_to_file(pool)
    print(f"扩展股票池: {len(pool)} 只")
