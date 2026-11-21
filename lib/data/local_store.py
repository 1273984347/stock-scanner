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


def _parquet_rows(p: Path) -> int:
    """读单个 parquet 的行数（元数据级，不加载列数据）。"""
    try:
        from pyarrow import parquet as _pq
        return int(_pq.ParquetFile(str(p)).metadata.num_rows)
    except Exception:
        try:
            return len(pd.read_parquet(p))
        except Exception:
            return 0


def _touch(dataset: str, rows_delta: int, key: str, new_file: bool = True) -> None:
    """记账（C15，2026-11-21）：
    * files 语义 = 「现存文件数」——重写已有文件不再 +1（原实现无条件 +1，
      files 变成累计写次数 ⇒ 与盘上真值漂移，C12 卫生项）；
    * rows 记 **delta**——调用方传入「本次写引起的行数变化」（新增 = 全量，
      重写 = 新行数 − 旧行数；json 文件恒为 1 行 ⇒ 重写 delta = 0）。
      原实现的「重写也整段累加」让同日重写的数据集 rows 翻倍
      （实测 snapshot 22316 vs 11158）。"""
    with _LOCK:
        m = _read_manifest()
        ds = m["datasets"].setdefault(dataset, {"files": 0, "rows": 0})
        if new_file:
            ds["files"] = ds.get("files", 0) + 1
        ds["rows"] = max(0, ds.get("rows", 0) + int(rows_delta))
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


def _disk_truth() -> dict:
    """按盘上真值统计各数据集（files=现存文件数，rows=parquet 行数合计 / json 记 1）。

    对账真源（C15，2026-11-21）：manifest 的 files/rows 是写路径的累计记账，
    与盘上清单会漂移（seed unpack 直写不入账、重写双计）；本函数即对账基准。
    """
    truth: dict = {}
    if not STORE_ROOT.exists():
        return truth
    for folder in sorted(STORE_ROOT.iterdir()):
        if not folder.is_dir():
            continue
        files = [p for p in folder.rglob("*")
                 if p.is_file() and not p.name.endswith(".tmp")]
        if not files:
            continue
        rows = 0
        for p in files:
            if p.suffix == ".parquet":
                try:
                    from pyarrow import parquet as _pq
                    rows += _pq.ParquetFile(str(p)).metadata.num_rows
                except Exception:
                    try:
                        rows += len(pd.read_parquet(p))
                    except Exception:
                        logger.warning(f"[local_store] 对账读行数失败: {p}")
            else:
                rows += 1
        truth[folder.name] = {"files": len(files), "rows": rows}
    return truth


def rebuild_manifest(dry_run: bool = False) -> dict:
    """按盘上真值重建 manifest（返回 {dataset: {files, rows}}）。

    保留既有 updated_at / last_key（freshness 语义不因对账重置——
    financial/fundflow/blocks 的 is_fresh 按 manifest updated_at 判 90 天窗口）；
    盘上有而 manifest 没有的数据集按 now() 记 updated_at。
    dry_run=True 只返回建议值，不写盘（--verify 用）。
    """
    truth = _disk_truth()
    if dry_run:
        return truth
    with _LOCK:
        m = _read_manifest()
        old = m.get("datasets", {})
        datasets = {}
        for ds, t in truth.items():
            prev = old.get(ds, {})
            datasets[ds] = {
                "files": t["files"],
                "rows": t["rows"],
                "updated_at": prev.get("updated_at")
                or datetime.now().isoformat(timespec="seconds"),
                "last_key": prev.get("last_key"),
            }
        m["datasets"] = datasets
        m["schema_version"] = SCHEMA_VERSION
        m["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _write_manifest(m)
        return datasets


# ---------------------------------------------------------------- 读写

def write_frame(dataset: str, key: str, df: pd.DataFrame) -> bool:
    """原子写入一个 DataFrame；空帧不落盘。"""
    if not enabled() or df is None or getattr(df, "empty", True):
        return False
    p = _path(dataset, key, ".parquet")
    with _LOCK:
        new_file = not p.exists()
        old_rows = 0 if new_file else _parquet_rows(p)
        _ensure_dir(p)
        tmp = p.with_suffix(".parquet.tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, p)
    _touch(dataset, len(df) - old_rows, key, new_file=new_file)
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
        new_file = not p.exists()
        _ensure_dir(p)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, p)
    _touch(dataset, 1 if new_file else 0, key, new_file=new_file)
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
