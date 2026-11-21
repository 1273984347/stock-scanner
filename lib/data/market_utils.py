"""market_utils.py — TDX 市场检测 + 路径解析 helper

修复 stock-scanner 项目中 7+ 处的错误市场检测模式：
- detect_market: 正确识别北证 (4/8 开头)，不再误归 sz
- get_tdx_path: 优先 lday_4 缺失则 fallback lday（兼容 bj 无前复权）
- read_tdx_day_closes: 高层函数，复用 tdx_batch._read_closes_only

历史问题：
- 旧代码 `market = "sh" if code.startswith(("6", "9")) else "sz"` 把 bj 股票
  （43xxxx, 83xxxx, 87xxxx, 88xxxx, 92xxxx）错归 sz，导致 bj/lday 数据
  永远走不到（sz/lday/sz430001.day 不存在 → silent skip）
- 旧代码硬编码 `lday_4/`，bj 无前复权数据时无法 fallback 到 lday/

单一真相源：TDX_DATA_PATH 从 config.py 读取，不再 os.environ.get 重复。
"""
from pathlib import Path
from typing import Optional
import logging
import os

import numpy as np

# 单一真相源：config.TDX_DATA_PATH 在模块导入时从环境变量读取。
# 为了让 monkeypatch 在测试中能覆盖，_tdx_root() 每次调用时重新读 env。
try:
    from config import TDX_DATA_PATH as _DEFAULT_TDX_PATH
except ImportError:
    _DEFAULT_TDX_PATH = "D:/TDX/vipdoc"

logger = logging.getLogger(__name__)


def _tdx_root() -> Path:
    """读取 TDX_DATA_PATH，每次调用重新读 env（支持测试 monkeypatch）"""
    return Path(os.environ.get("TDX_DATA_PATH", _DEFAULT_TDX_PATH))


# 股票代码前缀 → 市场映射
# 关键：bj 必须在 sh/sz 之前判断，否则 4/8 开头会落到 sz
# "92" 是北证原代码（在 9x 前缀中独占）
BJ_PREFIXES = ("4", "8", "92")
SH_PREFIXES = ("5", "6", "9")
SZ_PREFIXES = ("0", "2", "3")
# 其他（1x 债券、纯字母等）→ unknown，调用方走 fallback


def normalize_code(raw) -> str:
    """股票代码归一为 6 位纯数字（去 sh/sz/bj 前缀 + 补前导零）。

    **单一真源**：数据源落盘键（\u0060data_source_registry._store_key\u0060）、资金流
    批量层、实时行情共用本口径，避免各处各写一套造成 key 对不上。

    P0（2026-11-20）：资金流市场批量层的 code 列前导零在解析阶段即丢失
    （实测缓存 key 为 '1' / '10' / '858' …），而查询侧用 6 位代码 ⇒
    深市 000/002、创业板 300、北交所 4/8/92 批量层 100% MISSING，
    直接落默认 0 ⇒ 资金面维以「假零值」参与四维加权。

    Args:
        raw: 任意形态代码（int / '858' / '000858' / 'sz000858'）

    Returns:
        6 位纯数字字符串；非数字输入原样返回（不臆造代码）。
    """
    c = str(raw).strip().lower()
    if len(c) == 8 and c[:2] in ("sh", "sz", "bj"):
        c = c[2:]
    return c.zfill(6) if c.isdigit() else c


def detect_market(code: str) -> str:
    """根据股票代码判断市场。

    Args:
        code: 6 位股票代码（支持 strip 和 zfill）

    Returns:
        "sh" 沪市（60/68 主板、90 B股、5x 基金）
        "sz" 深市（00 主板、30 创业板、20 B股）
        "bj" 北证（43/83/87/88/92）
        "unknown" 债券或其他（1x、非法）

    Examples:
        >>> detect_market("600519")
        'sh'
        >>> detect_market("430001")
        'bj'
        >>> detect_market("000001")
        'sz'
        >>> detect_market("111111")
        'unknown'
    """
    c = code.strip().zfill(6)
    if not c.isdigit() or len(c) != 6 or c == "000000":
        return "unknown"
    if c.startswith(BJ_PREFIXES):
        return "bj"
    if c.startswith(SH_PREFIXES):
        return "sh"
    if c.startswith(SZ_PREFIXES):
        return "sz"
    return "unknown"


def volume_unit_scale(code_or_sym: str) -> float:
    """腾讯系 / westock 报文 volume 单位的板块规则（P-122，ADR-0036 D1）。

    沪主板/创业板报文 volume=手（×100 归一到股）；**科创板（688/689 开头）
    报文已是股**。2026-11-21 Wind 对照 + 原始报文实测：腾讯 sh688001 与
    westock 688001 原始值均为 10276186（=股，与 Wind 同值），而
    sh600519/sh600519(westock)=67229/42692（=手）——两家同一板块规则，
    一刀切 ×100 曾让 610 只科创板 volume 虚增 100 倍且不报错。

    Args:
        code_or_sym: 6 位代码或带前缀代码（如 ``688001`` / ``sh688001``）

    Returns:
        乘数：科创板 1.0，其余 100.0
    """
    c = str(code_or_sym).strip().lower()
    if c[:2] in ("sh", "sz", "bj"):
        c = c[2:]
    return 1.0 if c.startswith(("688", "689")) else 100.0


def get_tdx_path(code: str, prefer_qfq: bool = True) -> Optional[Path]:
    """返回 TDX .day 文件路径。

    Fallback 顺序（prefer_qfq=True）：
        lday_4/ (前复权) → lday/ (不复权) → None

    Args:
        code: 6 位股票代码
        prefer_qfq: 是否优先前复权文件（默认 True）

    Returns:
        Path 对象；文件不存在或代码无效时返回 None

    Examples:
        >>> get_tdx_path("600519")  # 走 lday_4/sh600519.day
        >>> get_tdx_path("430001")  # bj 无 lday_4，自动 fallback 到 lday/bj430001.day
    """
    market = detect_market(code)
    if market == "unknown":
        return None

    tdx_root = _tdx_root()
    primary_subdir = "lday_4" if prefer_qfq else "lday"
    primary = tdx_root / market / primary_subdir / f"{market}{code}.day"
    if primary.exists():
        return primary
    fallback = tdx_root / market / "lday" / f"{market}{code}.day"
    if fallback.exists():
        return fallback
    return None


def read_tdx_day_closes(code: str, days: int = 60) -> Optional[np.ndarray]:
    """读取 close 数组（已除以 100），封装 tdx_batch._read_closes_only 的 mmap 优化。

    Args:
        code: 6 位股票代码
        days: 读取天数（从末条往前 N 条）

    Returns:
        np.ndarray（dtype=float64）；文件不存在时返回 None

    Examples:
        >>> closes = read_tdx_day_closes("430001", days=20)
        >>> if closes is not None:
        ...     print(closes[-1], closes.mean())
    """
    from lib.data.tdx_batch import _read_closes_only

    fpath = get_tdx_path(code, prefer_qfq=True)
    if fpath is None:
        return None
    return _read_closes_only(fpath, days)


def read_tdx_day_ohlcv(code: str, days: int = 60) -> "Optional[pd.DataFrame]":
    """读取 OHLCV DataFrame（已除以 100），封装 tdx_batch._read_day_file_raw。

    替代 sector_rotation.py / sector_score.py / factors.py 中重复实现的 _read_tdx_day。

    Args:
        code: 6 位股票代码
        days: 读取天数

    Returns:
        pd.DataFrame with columns: date, open, high, low, close, amount, volume
        文件不存在或记录数 < max(days, 10) 时返回 None
    """
    from lib.data.tdx_batch import _read_day_file_raw

    fpath = get_tdx_path(code, prefer_qfq=True)
    if fpath is None:
        return None
    records = _read_day_file_raw(fpath, days)
    if records is None:
        return None
    import pandas as pd
    return pd.DataFrame(records)
