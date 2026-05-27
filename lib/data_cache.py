"""统一财务数据缓存

封装：
- 5 分钟内存 LRU
- 7 天磁盘 parquet
- dict 统一格式（来自 get_stock_fundamentals）
- pd.Series 兼容读取（来自 _get_financial_data 旧格式）
- _source / _fetch_method 可观测字段

Usage:
  from lib.data_cache import financial_cache
  financial_cache.set("600519", {"profitability": {...}, "_source": "tdx"})
  data = financial_cache.get("600519")  # 永远返回 dict 或 None
"""
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd

logger = logging.getLogger(__name__)

# ========== 路径配置 ==========
_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"


class FinancialCache:
    """统一财务数据缓存

    统一管理所有基本面数据的内存 + 磁盘缓存。
    写入：统一 dict 格式（{profitability, solvency, growth, _source}）
    读出：透明兼容 dict 和 pd.Series 两种格式
    """

    def __init__(self, memory_ttl: int = 300, disk_ttl: int = 604800):
        """
        Args:
            memory_ttl: 内存缓存有效期（秒），默认 5 分钟
            disk_ttl: 磁盘缓存有效期（秒），默认 7 天
        """
        self._memory: Dict[str, tuple] = {}  # code -> (data: dict, ts: float)
        self._memory_ttl = memory_ttl
        self._disk_ttl = disk_ttl

    def _disk_path(self, code: str) -> Path:
        return _CACHE_DIR / "fin" / f"{code}.parquet"

    # ─── 核心 API ───

    def get(self, code: str) -> Optional[Dict[str, Any]]:
        """读取缓存（优先内存，备选磁盘）

        Returns:
            dict 格式数据，或 None（无缓存 / 已过期）
        """
        now = time.time()

        # 1. 内存缓存
        if code in self._memory:
            data, ts = self._memory[code]
            if now - ts < self._memory_ttl:
                return self._normalize(data)
            del self._memory[code]

        # 2. 磁盘缓存
        p = self._disk_path(code)
        if p.exists():
            try:
                mtime = p.stat().st_mtime
                if now - mtime < self._disk_ttl:
                    df = pd.read_parquet(p)
                    if not df.empty:
                        row = df.iloc[0]
                        # 尝试从 DataFrame row 构建 dict
                        data = self._row_to_dict(row)
                        if data:
                            # 回填内存缓存
                            self._memory[code] = (data, now)
                            return data
            except Exception as e:
                logger.debug(f"[FinancialCache] 磁盘缓存读取失败 [{code}]: {e}")

        return None

    def set(self, code: str, data: Dict[str, Any]) -> None:
        """写入缓存（内存 + 磁盘）

        Args:
            data: dict 格式，必须包含 _source 字段
        """
        if not isinstance(data, dict) or "_source" not in data:
            logger.warning(f"[FinancialCache] 写入数据格式错误 [{code}]: 缺少 _source")
            return

        now = time.time()
        self._memory[code] = (dict(data), now)

        # 磁盘持久化
        try:
            p = self._disk_path(code)
            p.parent.mkdir(parents=True, exist_ok=True)
            df = pd.DataFrame([data])
            df.to_parquet(p, index=False)
        except Exception as e:
            logger.debug(f"[FinancialCache] 磁盘缓存写入失败 [{code}]: {e}")

    def has_valid(self, code: str) -> bool:
        """检查是否有未过期的缓存"""
        return self.get(code) is not None

    def invalidate(self, code: str) -> None:
        """手动失效缓存（内存 + 磁盘）"""
        self._memory.pop(code, None)
        p = self._disk_path(code)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    def clear_memory(self) -> None:
        """清空内存缓存（用于测试）"""
        self._memory.clear()

    # ─── 内部方法 ───

    def _normalize(self, data) -> Optional[Dict[str, Any]]:
        """将 pd.Series 或 dict 统一转为 dict 格式"""
        if data is None:
            return None
        if isinstance(data, dict):
            return self._dict_to_standard(data)
        if isinstance(data, pd.Series):
            return self._series_to_dict(data)
        return None

    def _dict_to_standard(self, data: dict) -> Optional[Dict[str, Any]]:
        """将 dict 转为标准格式"""
        src = data.get("_source")
        if src in ("tdx", "baostock", "akshare", "thshare"):
            return {
                "profitability": data.get("profitability") or {},
                "solvency": data.get("solvency") or {},
                "growth": data.get("growth") or {},
                "_source": src,
            }
        # 旧格式（pd.Series 展平的字段）
        if src:
            return {
                "profitability": {
                    "ROE": data.get("ROE") or data.get("roe"),
                    "gross_margin": data.get("gross_margin"),
                    "net_margin": data.get("net_margin") or data.get("net_margin"),
                    "operating_margin": None,
                    "ebitda_margin": None,
                },
                "solvency": {
                    "debt_to_asset": data.get("debt_to_asset") or data.get("_debt_to_asset"),
                    "debt_to_equity": data.get("debt_to_equity"),
                    "equity_multiplier": None,
                    "interest_coverage": None,
                },
                "growth": {
                    "revenue_growth": data.get("revenue_growth"),
                    "net_income_growth": data.get("net_income_growth") or data.get("_net_income_growth"),
                },
                "_source": src,
            }
        return None

    def _series_to_dict(self, s: pd.Series) -> Optional[Dict[str, Any]]:
        """将 pd.Series 转为标准 dict"""
        src = s.get("_source")
        if not src:
            return None
        return {
            "profitability": {
                "ROE": s.get("ROE") or s.get("roe"),
                "gross_margin": s.get("gross_margin"),
                "net_margin": s.get("net_margin"),
                "operating_margin": None,
                "ebitda_margin": None,
            },
            "solvency": {
                "debt_to_asset": s.get("debt_to_asset") or s.get("_debt_to_asset"),
                "debt_to_equity": s.get("debt_to_equity"),
                "equity_multiplier": None,
                "interest_coverage": None,
            },
            "growth": {
                "revenue_growth": s.get("revenue_growth"),
                "net_income_growth": s.get("net_income_growth") or s.get("_net_income_growth"),
            },
            "_source": src,
        }

    def _row_to_dict(self, row) -> Optional[Dict[str, Any]]:
        """将 DataFrame row 转为标准 dict"""
        try:
            data = row.to_dict()
            return self._dict_to_standard(data)
        except Exception:
            return None


# ─── 全局单例 ───
_financial_cache: Optional[FinancialCache] = None


def get_financial_cache() -> FinancialCache:
    global _financial_cache
    if _financial_cache is None:
        _financial_cache = FinancialCache()
    return _financial_cache
