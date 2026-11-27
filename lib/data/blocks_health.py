"""blocks 仓「新股覆盖」判据（P-147 防复发）

背景：`sync_blocks` 的覆盖率判据只看总量（写入数/上版数 >= 98%），不验成员。
新股陆续上市时，旧映射基数不增长 ⇒ 缺 23 只新股仍算 99.6% 通过，缺口静默存在 20 天。
本模块提供**成员级**判据：上市超过 N 个自然日的股票必须出现在 blocks 仓，否则报缺口。

设计取舍：
- 用**自然日**而非交易日：行业分类在招股阶段即确定，源站收录延迟以自然日衡量更直观；
  且避免交易日历降级（仅跳周末）导致的判据漂移。默认 10 自然日（约 7 个交易日）。
- 只报缺口、不自动改写：写仓由 `scripts/fill_missing_blocks.py` 负责（带行业名白名单校验），
  判据与修复分离，避免"判据自己改了数据"。
- 新股清单按天缓存到 cache/，防止每次巡检都打 4 个 akshare 接口。

依赖方向：lib/data 内，不 import 顶层包（符合 AGENTS.md §1.3）。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

# B 股与非 A 股代码段（blocks 仓含 B 股，但新股判据只关心 A 股新股）
_B_SHARE_PREFIXES = ("200", "201", "202", "203", "204", "205", "206", "207", "208", "209", "900")

_CACHE_TTL_S = 12 * 60 * 60  # 新股清单半天缓存


def _cache_path() -> Path:
    root = Path(__file__).resolve().parents[2]
    return root / "cache" / "blocks_newstock_universe.json"


def _load_universe_cached() -> List[Dict[str, str]]:
    """全市场股票清单（代码+上市日期），带磁盘缓存。失败返回空并 warning。"""
    path = _cache_path()
    now = time.time()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if now - float(raw.get("ts", 0)) < _CACHE_TTL_S:
                return raw.get("items", [])
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    try:
        import akshare as ak
    except ImportError:
        logger.warning("[blocks_health] akshare 不可用，无法做新股覆盖判据")
        return []

    items: List[Dict[str, str]] = []
    for fn, kw in [("stock_info_sh_name_code", {"symbol": "科创板"}),
                   ("stock_info_sh_name_code", {"symbol": "主板A股"}),
                   ("stock_info_sz_name_code", {"symbol": "A股列表"}),
                   ("stock_info_bj_name_code", {})]:
        try:
            df = getattr(ak, fn)(**kw)
            code_col = next(c for c in df.columns if "代码" in c)
            date_col = next(c for c in df.columns if "日期" in c or "时间" in c)
            for _, row in df.iterrows():
                code = str(row[code_col]).zfill(6)
                list_date = str(row[date_col])[:10]
                if len(code) == 6 and code.isdigit():
                    items.append({"code": code, "list_date": list_date})
        except Exception as exc:  # noqa: BLE001 - 单板块失败不阻断整体判据
            logger.warning("[blocks_health] %s 清单获取失败: %s", fn, exc)

    # 去重
    dedup: Dict[str, str] = {}
    for it in items:
        dedup.setdefault(it["code"], it["list_date"])
    out = [{"code": c, "list_date": d} for c, d in dedup.items()]

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": now, "items": out}, ensure_ascii=False),
                        encoding="utf-8")
    except OSError:
        pass
    return out


def _is_a_share(code: str) -> bool:
    return code.isdigit() and len(code) == 6 and not code.startswith(_B_SHARE_PREFIXES)


def check_newstock_coverage(min_listed_days: int = 10, today: date | None = None,
                            blocks_dir: Path | None = None) -> Dict:
    """新股覆盖判据：上市 >= min_listed_days 自然日的 A 股必须在 blocks 仓出现。

    Args:
        min_listed_days: 上市天数阈值（自然日）。行业分类在招股阶段确定，
            10 天足够源站收录；调小会更早暴露但可能误报。
        today: 判定基准日（测试注入用），默认今天。
        blocks_dir: blocks 目录（测试注入用），默认 data/store/blocks。

    Returns:
        {
          "ok": bool,              # True = 无缺口
          "checked": int,          # 参与判定的新股数
          "missing": [{code, list_date, days_listed}],
          "known_codes": int,      # blocks 仓文件数
          "reason": str,           # ok=False 的人读原因
        }
    """
    root = Path(__file__).resolve().parents[2]
    bdir = blocks_dir or (root / "data" / "store" / "blocks")
    base_day = today or datetime.now().date()

    if not bdir.is_dir():
        return {"ok": False, "checked": 0, "missing": [], "known_codes": 0,
                "reason": f"blocks 目录不存在: {bdir}"}

    existing = {f.stem for f in bdir.glob("*.json")}
    universe = _load_universe_cached()
    if not universe:
        return {"ok": False, "checked": 0, "missing": [], "known_codes": len(existing),
                "reason": "新股清单不可用（akshare 失败），判据无法执行——不得视为通过"}

    missing = []
    checked = 0
    for item in universe:
        code, list_date = item["code"], item["list_date"]
        if not _is_a_share(code):
            continue
        try:
            listed = datetime.strptime(list_date, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        days_listed = (base_day - listed).days
        if days_listed < min_listed_days:
            continue
        checked += 1
        if code not in existing:
            missing.append({"code": code, "list_date": list_date, "days_listed": days_listed})

    missing.sort(key=lambda x: x["list_date"])
    ok = not missing
    reason = "" if ok else (
        f"{len(missing)} 只上市>={min_listed_days} 自然日的 A 股缺 blocks 映射；"
        f"修复：py scripts/fill_missing_blocks.py --write")
    return {"ok": ok, "checked": checked, "missing": missing,
            "known_codes": len(existing), "reason": reason}
