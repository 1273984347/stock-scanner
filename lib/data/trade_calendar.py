"""交易日历 —— A 股交易日判定的单一真源。

架构评审（docs/audit/architecture-review-2026-11-20.md）P0 修复批次引入。
此前交易日判定散落三处且全部失真（坑表 P-108）：
- ``scripts/daily_scan.py`` 硬编码 ``D:/TDX/vipdoc/sh/lday_4/sh600000.day``——该包
  2026-11 起停更（``update_tdx_data.py`` 注释禁用）且目录已随 ADR-0017 删除，
  文件缺失时兜底「仅跳过周末」→ **节假日的工作日照常跑、推陈旧信号**；
- ``reporter/scheduler.py`` 只看周末 → 晨报/策略报告节假日照推；
- ``run.py`` 盘后扫描无任何交易日门。

数据源：akshare 新浪交易日历（``tool_trade_date_hist_sina``，覆盖 1990-12-19 至
当年年末，含全年已排定交易日）。本地 JSON 缓存 + 挂起守卫（ADR-0019 daemon 模式）
+ 失败显式退化：日历缺失/未覆盖当天时退化为「仅跳过周末」并 **warning 留痕**，
绝不静默。

写路径纪律：缓存文件派生自 ``data_layer.CACHE_DIR``（调用时取值）——conftest
``_isolate_repo_cache_dirs`` 重定向该属性后，测试写路径自动落 tmp（坑表 P-80）。
"""

import json
import logging
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)

# 挂起守卫：新浪日历接口无显式超时承诺，照 ADR-0019 模式 daemon 线程 join。
_CALENDAR_FETCH_TIMEOUT_S = 15.0
# 刷取失败后的进程内冷却，冷却期内不再打网（ADR-0018 负路径标记同型）。
_REFRESH_RETRY_INTERVAL_S = 300.0

_lock = threading.Lock()
# 进程级单例（坑表 P-100 纪律：测试经 reset_for_tests() 按用例清零）。
_trade_days: Optional[Set[date]] = None
_max_day: Optional[date] = None
_last_refresh_fail_monotonic: float = 0.0


def reset_for_tests() -> None:
    """清零进程级单例状态（P-100：进程级单例必须按用例清零）。"""
    global _trade_days, _max_day, _last_refresh_fail_monotonic
    _trade_days = None
    _max_day = None
    _last_refresh_fail_monotonic = 0.0


def _cache_file() -> Path:
    """缓存文件路径 —— 调用时取 ``data_layer.CACHE_DIR``（继承 conftest 隔离）。"""
    from lib.data.data_layer import CACHE_DIR

    return Path(CACHE_DIR) / "calendar" / "trade_days.json"


def _fetch_trade_days() -> Set[date]:
    """拉取新浪交易日历；超时/网络错误/解析失败一律抛异常，由调用方决策。

    守卫模式与 ``data_source_registry._call_source_with_timeout`` 同源（ADR-0019）：
    daemon 线程 join 超时不 kill（泄漏线程随进程回收），超时计为本次失败。
    """
    import akshare as ak

    def _call() -> Set[date]:
        df = ak.tool_trade_date_hist_sina()
        col = "trade_date" if "trade_date" in df.columns else df.columns[0]
        return set(pd.to_datetime(df[col]).dt.date.unique())

    box: dict = {}

    def _runner() -> None:
        try:
            box["days"] = _call()
        except BaseException as exc:  # noqa: BLE001 —— 与 registry 超时守卫同口径
            box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True, name="trade-calendar-fetch")
    t.start()
    t.join(_CALENDAR_FETCH_TIMEOUT_S)
    if t.is_alive():
        raise TimeoutError(
            f"trade calendar fetch timed out after {_CALENDAR_FETCH_TIMEOUT_S}s (hang)"
        )
    if "error" in box:
        raise box["error"]
    return box["days"]


def _write_cache(days: Set[date]) -> None:
    """原子写缓存（tmp + os.replace，config.write_config_overrides 同型）。"""
    path = _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": date.today().isoformat(),
        "source": "akshare.tool_trade_date_hist_sina",
        "trade_days": sorted(d.isoformat() for d in days),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _load_from_disk() -> bool:
    """读盘填充进程单例；文件缺失/损坏返回 False（不抛，由上层走刷取/退化）。"""
    global _trade_days, _max_day
    try:
        path = _cache_file()
        if not path.exists():
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        days = {date.fromisoformat(s) for s in payload["trade_days"]}
        if not days:
            return False
        _trade_days = days
        _max_day = max(days)
        return True
    except Exception as exc:  # noqa: BLE001 —— 损坏缓存按 miss 处理，与 data_layer 同语义
        logger.warning("trade_calendar: 缓存读取失败按 miss 处理: %s", exc)
        return False


def _refresh_locked() -> bool:
    """刷取并落盘（调用方须已持锁）；失败记冷却时间戳并 warning。"""
    global _trade_days, _max_day, _last_refresh_fail_monotonic
    try:
        days = _fetch_trade_days()
    except Exception as exc:  # noqa: BLE001
        _last_refresh_fail_monotonic = time.monotonic()
        logger.warning("trade_calendar: 刷取失败（%.0fs 冷却内不重试）: %s",
                       _REFRESH_RETRY_INTERVAL_S, exc)
        return False
    _trade_days = days
    _max_day = max(days)
    try:
        _write_cache(days)
    except OSError as exc:
        # 落盘失败不影响本次判定（内存单例已可用），仅留痕
        logger.warning("trade_calendar: 缓存落盘失败（内存态可用）: %s", exc)
    return True


def is_trading_day(d: Optional[date] = None) -> bool:
    """判断 ``d``（默认今天）是否为 A 股交易日。

    降级链：周末短路 → 进程单例 → 磁盘缓存 → 刷一次 → 仍不覆盖则
    **显式退化**为「仅跳过周末」（工作日按交易日放行）并 warning。
    退化方向与历史兜底一致（不改变行为），区别是留痕且可观测。
    """
    target = d or date.today()
    if target.weekday() >= 5:
        return False

    with _lock:
        if _trade_days is None:
            _load_from_disk()
        if _trade_days is not None and _max_day >= target:
            return target in _trade_days

        # 缓存缺失或未覆盖 target：冷却外刷一次
        if time.monotonic() - _last_refresh_fail_monotonic >= _REFRESH_RETRY_INTERVAL_S:
            _refresh_locked()
        if _trade_days is not None and _max_day >= target:
            return target in _trade_days

        logger.warning(
            "trade_calendar: 日历缺失或未覆盖 %s（缓存 max=%s），"
            "退化为周末判断（工作日按交易日放行）",
            target.isoformat(),
            _max_day.isoformat() if _max_day else None,
        )
        return True


def calendar_health() -> dict:
    """观测出口：供心跳哨兵/运维端点查询日历新鲜度（只读，不触发刷网）。"""
    with _lock:
        if _trade_days is None:
            _load_from_disk()
        return {
            "cached_days": len(_trade_days) if _trade_days else 0,
            "max_date": _max_day.isoformat() if _max_day else None,
            "cache_file": str(_cache_file()),
            "refresh_cooldown_until_monotonic": (
                _last_refresh_fail_monotonic + _REFRESH_RETRY_INTERVAL_S
                if _last_refresh_fail_monotonic else 0.0
            ),
        }


def preheat() -> dict:
    """预刷日历缓存（调度任务用，C16 挂账收口，2026-11-22）：无条件强制刷一次。

    新进程首刷可能瞬态失败（15s 超时 → 300s 冷却，C16 断网演练实测）；
    bootstrap 的 StoreDataSync 链在同步/晨报前先调一次，把瞬态失败隔离在
    低价值任务里（失败不抛——返回 calendar_health() 留痕到任务日志，后续
    is_trading_day 走既有降级链并 warning）。
    """
    with _lock:
        _load_from_disk()
        _refresh_locked()
    return calendar_health()
