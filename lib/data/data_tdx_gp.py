"""TDX 高频Tick数据（tdxgp）解析器

数据路径：D:/TDX/vipdoc/gp/

文件格式（已逆向破解）：
  - 1 字节 header（版本/市场标识）
  - 26401 条记录 × 13 字节（以 gpsz000001.dat 为例）
  - 每条记录：4 字节日期(uint32 LE) + 9 字节数据 + 1 字节类型标识

  记录类型分布（gpsz000001，共 43 种类型）：
    - 0x01 (115条): 年报/季报财务数据（季度截止日）
    - 0x02 (1条): 特殊标记
    - 0x03 (2000条): 日线指标A
    - 0x04 (210条): 日线指标B（价格相关?）
    - 0x05 (30条): 日线指标C
    - 0x06 (1757条): 日线指标D
    - 0x07-0x2F: 各种市场统计指标

  9字节数据字段格式（按类型不同有不同的解释）：
    - 字节 0-3: 4字节 float（主值）
    - 字节 4-5: 2字节 uint16（辅值）
    - 字节 6-8: 3字节数据（类型特定）

  文件名前缀格式：
    - gpbjXXXXXX.dat: 北交所
    - gpszXXXXXX.dat: 深圳
    - gpszshXXXXXX.dat: 上海
"""
import struct
import logging
from pathlib import Path
from typing import Optional, Dict, List
import pandas as pd

logger = logging.getLogger(__name__)

TDX_GP_PATH = Path("D:/TDX/vipdoc/gp")

# 记录类型名称映射（基于逆向分析）
RECORD_TYPE_NAMES = {
    0x01: "annual_quarterly_fin",   # 年报/季报财务
    0x02: "special_marker",          # 特殊标记
    0x03: "daily_metric_a",          # 日线指标A
    0x04: "daily_metric_b",          # 日线指标B（价格相关）
    0x05: "daily_metric_c",          # 日线指标C
    0x06: "daily_metric_d",          # 日线指标D
    0x07: "money_flow",             # 资金流
    0x08: "pct_change",            # 涨跌幅
    0x09: "up_down_flag",          # 涨跌标记
    0x0B: "volume_metric_a",       # 成交量指标A
    0x0C: "volume_metric_b",       # 成交量指标B
    0x0D: "volume_metric_c",        # 成交量指标C
    0x0E: "mixed_metric",           # 混合指标
    0x0F: "mixed_metric_b",         # 混合指标B
    0x10: "volume_metric_d",        # 成交量指标D
    0x11: "price_metric_a",        # 价格指标A
    0x12: "price_metric_b",        # 价格指标B
    0x13: "turnover_rate",         # 换手率
    0x14: "turnover_rate_b",       # 换手率B
    0x15: "turnover_rate_c",       # 换手率C
    0x16: "sentiment_metric",       # 情绪指标
    0x18: "mixed_metric_c",         # 混合指标C
    0x19: "amount_metric",          # 成交额指标
    0x1B: "amount_metric_b",        # 成交额指标B
    0x1D: "index_metric",           # 指数指标
    0x1E: "special_value",          # 特殊值
    0x1F: "price_metric_c",         # 价格指标C
    0x20: "price_metric_d",         # 价格指标D
    0x21: "single_value",          # 单值
    0x22: "zero_marker",           # 零标记
    0x23: "amount_metric_c",        # 成交额指标C
    0x24: "status_indicator",      # 状态指示
    0x25: "empty_marker",          # 空标记
    0x26: "flag_metric",          # 标志指标
    0x27: "flag_metric_b",         # 标志指标B
    0x28: "flag_metric_c",         # 标志指标C
    0x29: "special_date",         # 特殊日期
    0x2A: "turnover_metric",       # 换手指标
    0x2B: "mixed_flag",            # 混合标志
    0x2C: "count_metric",          # 计数指标
    0x2D: "ratio_metric",          # 比率指标
    0x2F: "recent_daily",         # 最近日线（最新）
}

RECORD_TYPE_DESCRIPTIONS = {
    0x01: "年报/季报财务数据（季度截止日，每年4条）",
    0x02: "特殊标记（单条）",
    0x03: "日线指标A（约2000条，2024-2026）",
    0x04: "日线指标B（约210条，2026最新，价格相关?）",
    0x05: "日线指标C（约30条）",
    0x06: "日线指标D（约1757条，2021-2026）",
    0x07: "资金流数据（约540条）",
    0x08: "涨跌幅指标（约31条）",
    0x09: "涨跌标记（约31条，二值）",
    0x0B: "成交量指标A（约2000条）",
    0x0C: "成交量指标B（约2000条）",
    0x0D: "成交量指标C（约2000条）",
    0x0E: "混合指标（约4条）",
    0x0F: "混合指标B（约5条）",
    0x10: "成交量指标D（约2000条）",
    0x11: "价格指标A（约37条）",
    0x12: "价格指标B（约2条）",
    0x13: "换手率（约419条）",
    0x14: "换手率B（约419条）",
    0x15: "换手率C（约2000条）",
    0x16: "情绪指标（约5条）",
    0x18: "混合指标C（约4条）",
    0x19: "成交额指标（约2000条）",
    0x1B: "成交额指标B（约2000条）",
    0x1D: "指数指标（约37条）",
    0x1E: "特殊值（约28条）",
    0x1F: "价格指标C（约1999条）",
    0x20: "价格指标D（约794条）",
    0x21: "单值（1条）",
    0x22: "零标记（1条）",
    0x23: "成交额指标C（约30条）",
    0x24: "状态指示（约380条）",
    0x25: "空标记（1条）",
    0x26: "标志指标（约1807条）",
    0x27: "标志指标B（约868条）",
    0x28: "标志指标C（约472条）",
    0x29: "特殊日期（约5条）",
    0x2A: "换手指标（约13条）",
    0x2B: "混合标志（约5条）",
    0x2C: "计数指标（1条）",
    0x2D: "比率指标（约230条）",
    0x2F: "最近日线（约118条，2025-2026）",
}


def list_gp_files() -> list:
    """列出所有gp文件"""
    return list(TDX_GP_PATH.glob("*.dat"))


def _detect_market(filename: str) -> str:
    """检测市场"""
    if filename.startswith("gpbj"):
        return "bj"
    elif filename.startswith("gpszsh"):
        return "sh"
    elif filename.startswith("gpsz"):
        return "sz"
    return "unknown"


def _parse_gp_record(rec: bytes) -> Optional[Dict]:
    """解析单条 GP 记录（13字节）

    Returns:
        dict with keys: date (int YYYYMMDD), value_main (float), value_aux (int),
                        raw_bytes (hex str), record_type (int), type_name (str)
    """
    if len(rec) < 13:
        return None
    date_val = struct.unpack('<I', rec[0:4])[0]
    value_main = struct.unpack('<f', rec[4:8])[0]
    value_aux = struct.unpack('<H', rec[8:10])[0]
    record_type = rec[12]
    return {
        "date": date_val,
        "value_main": value_main,
        "value_aux": value_aux,
        "raw_bytes": rec[4:12].hex(),
        "record_type": record_type,
        "type_name": RECORD_TYPE_NAMES.get(record_type, f"type_{record_type:02X}"),
    }


def _parse_gp_file(filepath: Path, record_types: Optional[List[int]] = None) -> pd.DataFrame:
    """解析 gp .dat 文件

    Args:
        filepath: gp文件路径
        record_types: 可选，只解析指定类型（如 [0x01, 0x2F]）

    Returns:
        DataFrame with columns: date, value_main, value_aux, record_type, type_name, raw_bytes
    """
    try:
        records = []
        with open(filepath, "rb") as f:
            data = f.read()

        # 1字节 header + N × 13字节记录
        for i in range(1, len(data), 13):
            chunk = data[i:i+13]
            if len(chunk) < 13:
                break
            parsed = _parse_gp_record(chunk)
            if parsed is None:
                continue
            if record_types is not None and parsed["record_type"] not in record_types:
                continue
            parsed["offset"] = i
            records.append(parsed)

        df = pd.DataFrame(records)
        if not df.empty:
            df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"解析 {filepath} 失败: {e}")
        return pd.DataFrame()


def _build_gp_pattern(code: str, market: str) -> str:
    """构建 GP 文件匹配模式（保持完整代码，不去除前导零）

    实际文件名前缀：
      - gpshXXXXXX.dat: 上海 ETF（510xxx）
      - gpszXXXXXX.dat: 深圳股票
      - gpbjXXXXXX.dat: 北交所
    """
    code = code.strip().zfill(6)
    if market == "sh":
        return f"gpsh{code}*.dat"
    elif market == "sz":
        return f"gpsz{code}*.dat"
    elif market == "bj":
        return f"gpbj{code}*.dat"
    return ""


def get_gp_data(code: str, market: str = "sz",
                record_types: Optional[List[int]] = None) -> pd.DataFrame:
    """获取指定股票的高频Tick数据

    Args:
        code: 股票代码（纯数字，如 "000001"）
        market: 市场（sh/sz/bj）
        record_types: 可选，只解析指定类型
                     1 = 年报/季报财务（0x01）
                     47 = 最近日线（0x2F）
                     3 = 日线指标A（0x03）
                     None = 所有类型
    """
    pattern = _build_gp_pattern(code, market)
    if not pattern:
        return pd.DataFrame()

    files = list(TDX_GP_PATH.glob(pattern))
    if not files:
        return pd.DataFrame()

    return _parse_gp_file(files[0], record_types=record_types)


def get_gp_info(filepath: Path) -> Dict:
    """获取 GP 文件的元信息

    Returns:
        dict with file_size, record_count, types distribution
    """
    try:
        with open(filepath, "rb") as f:
            data = f.read()

        from collections import Counter
        types = Counter()
        dates = []
        for i in range(1, len(data), 13):
            chunk = data[i:i+13]
            if len(chunk) < 13:
                break
            types[chunk[12]] += 1
            dates.append(struct.unpack('<I', chunk[0:4])[0])

        return {
            "file_size": len(data),
            "record_count": sum(types.values()),
            "type_count": len(types),
            "date_range": (min(dates), max(dates)) if dates else (None, None),
            "types": dict(sorted(types.items())),
        }
    except Exception as e:
        logger.error(f"获取 GP 文件信息失败: {e}")
        return {}


def get_gp_record_types() -> Dict[int, str]:
    """获取所有 GP 记录类型及其描述"""
    return RECORD_TYPE_DESCRIPTIONS
