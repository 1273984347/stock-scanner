"""项目内本地数据仓（ADR-0033）。

定位与 ``cache/`` 的区别：

| | `cache/` | `data/store/` |
|:--|:--|:--|
| 语义 | TTL 缓存，过期即删 | **项目数据**，长期保留 |
| 清理 | `clean_cache()` 按时间/体积淘汰 | 不被缓存清理触碰 |
| 离线 | 过期后无数据 | 断网仍可读 |
| 目的 | 省一次网络 | 让系统不依赖在线源 |

布局：

```
data/store/
├── manifest.json                  # 每个数据集的更新时间/行数/文件数
├── kline/{code}.parquet           # 个股日线（date/open/high/low/close/volume）
├── index/{code}.parquet           # 指数日线
├── snapshot/{YYYY-MM-DD}.parquet  # 全市场快照
├── events/{zt|lhb}/{YYYY-MM-DD}.parquet
├── financial/{code}.json          # 基本面
├── fundflow/{code}.json
└── blocks/{code}.json             # 板块归属
```

开关（环境变量）：

* ``QUANT_LOCAL_STORE=off`` —— 整体禁用（L3/L4 取证链路必须禁用，
  否则基线会被本地数据替换）；
* ``QUANT_OFFLINE=1`` —— 只读本地，不发起任何在线请求。
"""
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

STORE_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "store"
MANIFEST_PATH = STORE_ROOT / "manifest.json"
SCHEMA_VERSION = 1

_LOCK = threading.RLock()


# ---------------------------------------------------------------- 开关

def enabled() -> bool:
    """本地仓是否可用（取证链路通过 QUANT_LOCAL_STORE=off 关闭）。"""
    return os.environ.get("QUANT_LOCAL_STORE", "on").lower() not in ("0", "off", "false", "no")


def offline() -> bool:
    """离线模式：只读本地仓，禁止在线请求。"""
    return os.environ.get("QUANT_OFFLINE", "0").lower() in ("1", "on", "true", "yes")


# ---------------------------------------------------------------- 路径

def _path(dataset: str, key: str, ext: Optional[str] = None) -> Path:
    ext = ext or (".json" if dataset in ("financial", "fundflow", "blocks") else ".parquet")
    return STORE_ROOT / dataset / f"{key}{ext}"


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- manifest

def _read_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"schema_version": SCHEMA_VERSION, "datasets": {}}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        logger.warning("[local_store] manifest 损坏，重建")
        return {"schema_version": SCHEMA_VERSION, "datasets": {}}


def _write_manifest(manifest: dict) -> None:
    _ensure_dir(MANIFEST_PATH)
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, MANIFEST_PATH)


def _touch(dataset: str, rows: int, key: str) -> None:
    with _LOCK:
        m = _read_manifest()
        ds = m["datasets"].setdefault(dataset, {"files": 0, "rows": 0})
        ds["files"] = ds.get("files", 0) + 1
        ds["rows"] = ds.get("rows", 0) + int(rows)
        ds["updated_at"] = datetime.now().isoformat(timespec="seconds")
        ds["last_key"] = str(key)
        m["updated_at"] = ds["updated_at"]
        m["schema_version"] = SCHEMA_VERSION
        _write_manifest(m)


def coverage() -> dict:
    """各数据集的落盘概况（供 CLI/判据读取）。"""
    m = _read_manifest()
    out = {}
    for ds, info in sorted(m.get("datasets", {}).items()):
        folder = STORE_ROOT / ds
        files = list(folder.glob("*")) if folder.exists() else []
        out[ds] = {
            "files": len(files),
            "rows": info.get("rows", 0),
            "updated_at": info.get("updated_at"),
            "last_key": info.get("last_key"),
        }
    return out


# ---------------------------------------------------------------- 读写

def write_frame(dataset: str, key: str, df: pd.DataFrame) -> bool:
    """原子写入一个 DataFrame；空帧不落盘。"""
    if not enabled() or df is None or getattr(df, "empty", True):
        return False
    p = _path(dataset, key, ".parquet")
    with _LOCK:
        _ensure_dir(p)
        tmp = p.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, p)
    _touch(dataset, len(df), key)
    return True


def read_frame(dataset: str, key: str) -> pd.DataFrame:
    """读取一个 DataFrame；不存在或禁用时返回空帧（不抛异常）。"""
    if not enabled():
        return pd.DataFrame()
    p = _path(dataset, key, ".parquet")
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except (OSError, ValueError) as e:
        logger.warning(f"[local_store] 读取失败 {dataset}/{key}: {e}")
        return pd.DataFrame()


def write_json(dataset: str, key: str, obj: Any) -> bool:
    if not enabled() or not obj:
        return False
    p = _path(dataset, key, ".json")
    with _LOCK:
        _ensure_dir(p)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, p)
    _touch(dataset, 1, key)
    return True


def read_json(dataset: str, key: str) -> Optional[Any]:
    if not enabled():
        return None
    p = _path(dataset, key, ".json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------- 新鲜度

def last_trading_day(now: Optional[datetime] = None) -> date:
    """最近一个可能有收盘数据的交易日（工作日 + 15:30 规则；不含节假日表）。"""
    now = now or datetime.now()
    d = now.date()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def is_fresh(dataset: str, key: str, now: Optional[datetime] = None) -> bool:
    """按数据集语义判断本地数据是否够新。

    * kline / index：最后一根日期 >= 最近交易日（允许当日盘前取到上一交易日）
    * snapshot / events：文件名日期 == 最近交易日
    * financial / fundflow / blocks：manifest 更新时间在 90 天内
    """
    if not enabled():
        return False
    expect = last_trading_day(now)
    if dataset.startswith("kline") or dataset == "index":
        df = read_frame(dataset, key)
        if df.empty or "date" not in df.columns:
            return False
        try:
            return pd.to_datetime(df["date"]).max().date() >= expect
        except (ValueError, TypeError):
            return False
    if dataset == "snapshot":
        return key == expect.isoformat()
    if dataset == "events":
        return key.split("/")[-1] == expect.isoformat()
    if dataset in ("financial", "fundflow", "blocks"):
        m = _read_manifest().get("datasets", {}).get(dataset, {})
        ts = m.get("updated_at")
        if not ts:
            return False
        try:
            return datetime.now() - datetime.fromisoformat(ts) < timedelta(days=90)
        except ValueError:
            return False
    return False
