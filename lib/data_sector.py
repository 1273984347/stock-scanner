"""行业板块数据

数据来源：
- TDX（pytdx）：get_block_info — 行业板块成分股
- 腾讯行情 field 61：股票所属板块代码（目前为通用代码，扩展用）
- 东方财富（待）：行业分类 API

Usage:
  from lib.data_sector import get_stock_sector, get_sector_stocks
"""
import logging
from typing import Optional, List, Dict

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


def get_tencent_sector_code(code: str) -> Optional[str]:
    """从腾讯行情获取股票所属板块代码

    Note: field 61 目前返回通用代码（如"GP-A"），不适合细粒度分类
    """
    try:
        import requests
        sym = f"sh{code}" if code.startswith(("6", "5")) else f"sz{code}"
        url = f"https://qt.gtimg.cn/q=s_{sym}"
        resp = requests.get(url, timeout=5)
        resp.encoding = "gbk"
        parts = resp.text.split("~")
        if len(parts) > 10:
            sector_code = parts[10].strip()
            return sector_code if sector_code else None
    except Exception as e:
        logger.debug(f"[sector] 腾讯板块代码获取失败 [{code}]: {e}")
    return None


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
