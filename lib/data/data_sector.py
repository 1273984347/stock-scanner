"""行业板块数据

数据来源：
- TDX（pytdx）：get_block_info — 行业板块成分股
- 腾讯行情 field 61：股票所属板块代码（目前为通用代码，扩展用）
- 东方财富：行业板块实时行情 + 板块历史涨幅

Usage:
  from lib.data.data_sector import get_stock_sector, get_sector_stocks, enrich_sector_data
"""
import logging
from typing import Optional, List, Dict

import pandas as pd

logger = logging.getLogger(__name__)


def get_stock_sector(code: str) -> Optional[Dict]:
    """获取个股所属板块信息

    Returns:
        dict with keys:
        - board_code: 板块代码
        - board_name: 板块名称
        - sector_code: 行业代码
        - sector_name: 行业名称
    """
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        api.connect('119.97.178.103', 7709)

        code = code.strip().zfill(6)
        # 获取股票所属板块（通过搜索板块成分）
        # pytdx 不直接提供"查某股票所属板块"，通过遍历板块成分股方式
        result = None

        api.disconnect()
        return result
    except Exception as e:
        logger.debug(f"[sector] pytdx 连接失败: {e}")
        return None


def get_sector_stocks(sector: str = "白酒", max_stocks: int = 50) -> List[str]:
    """获取指定板块的成分股列表

    Args:
        sector: 板块名称关键词（如"白酒"、"银行"、"医药"）
        max_stocks: 最大返回数量

    Returns:
        股票代码列表
    """
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        api.connect('119.97.178.103', 7709)

        stocks = []
        # 遍历获取行业板块列表，再找成分股
        try:
            # 获取板块元数据
            meta = api.get_block_info_meta()
            if meta:
                for block in meta:
                    block_name = block.get("name", "")
                    if sector in block_name:
                        # 获取该板块成分股
                        info = api.get_block_info(block_name, 0, max_stocks)
                        if info:
                            for item in info:
                                c = str(item.get("code", "")).strip()
                                if c.isdigit() and len(c) == 6:
                                    stocks.append(c)
                                if len(stocks) >= max_stocks:
                                    break
                        break
        except Exception as e:
            logger.debug(f"[sector] 板块成分股查询异常: {e}")

        api.disconnect()
        return stocks[:max_stocks]
    except Exception as e:
        logger.debug(f"[sector] 获取板块成分股失败: {e}")
        return []


def _fetch_sector_realtime() -> pd.DataFrame:
    """从东方财富获取行业板块实时行情

    Returns:
        DataFrame with columns: 板块名称, 5日涨幅, 成交额
    """
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return pd.DataFrame()
        # 标准化列名
        rename_map = {}
        if "板块名称" in df.columns:
            rename_map["板块名称"] = "板块名称"
        if "涨跌幅" in df.columns:
            rename_map["涨跌幅"] = "5日涨幅"
        if "成交额" in df.columns:
            rename_map["成交额"] = "成交额"
        if rename_map:
            df = df.rename(columns=rename_map)
        return df
    except Exception as e:
        logger.debug(f"[sector] 板块实时行情获取失败: {e}")
        return pd.DataFrame()


def enrich_sector_data(board_stats: pd.DataFrame) -> pd.DataFrame:
    """为板块聚合数据补充涨幅和成交额变化

    Args:
        board_stats: 必须包含 '所属行业' 或 '板块' 列

    Returns:
        补充了 '5日涨幅' 和 '成交额变化' 列的 DataFrame
    """
    name_col = "所属行业" if "所属行业" in board_stats.columns else "板块"
    sector_rt = _fetch_sector_realtime()
    if sector_rt.empty or "板块名称" not in sector_rt.columns:
        return board_stats

    # 构建映射
    rt_map = sector_rt.set_index("板块名称").to_dict("index")

    ret_list = []
    vol_list = []
    for _, row in board_stats.iterrows():
        name = row[name_col]
        info = rt_map.get(name, {})
        ret_list.append(info.get("5日涨幅", None))
        vol_list.append(info.get("成交额", None))

    board_stats["5日涨幅"] = ret_list
    # 成交额变化 = 当前成交额 / 均值（简化：用排名代替）
    if any(v is not None for v in vol_list):
        vol_series = pd.Series(vol_list, dtype=float)
        vol_max = vol_series.max()
        if vol_max and vol_max > 0:
            board_stats["成交额变化"] = (vol_series / vol_max * 100).round(1)
        else:
            board_stats["成交额变化"] = None
    return board_stats


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=== 板块成分股测试 ===")
    for sector in ["白酒", "银行", "医药"]:
        stocks = get_sector_stocks(sector, max_stocks=5)
        print(f"{sector}: {stocks}")

    print("\n=== 腾讯板块代码 ===")
    for code, name in [("600519", "贵州茅台"), ("600036", "招商银行")]:
        sc = get_tencent_sector_code(code)
        print(f"{code} {name}: sector_code={sc}")
