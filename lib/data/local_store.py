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
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from lib.infra.file_lock import cross_process_lock

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


def _manifest_lock_path() -> Path:
    """manifest 跨进程互斥锁文件（Δ4，2026-11-22）——调用时取值，
    随 STORE_ROOT monkeypatch 落 tmp（坑表 P-80 测试隔离纪律）。"""
    return STORE_ROOT / ".manifest.lock"


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
    # Δ4：tmp 名带 pid+uuid——固定名会被另一进程同窗覆盖（os.replace 消费掉
    # 对方刚写好的 tmp）。调用方（_touch/rebuild_manifest）已持跨进程锁。
    tmp = MANIFEST_PATH.with_name(
        f"{MANIFEST_PATH.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, MANIFEST_PATH)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


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
    # Δ4（2026-11-22）：RMW 全程持跨进程锁——manifest 整文件读改写此前只有
    # 线程锁，API 进程与 data_sync 并发写时 last-writer-wins 丢更新（账实漂移）。
    # 跨进程锁在外层、线程锁在内层，全仓顺序一致防死锁。
    with cross_process_lock(_manifest_lock_path()):
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
            # 整批刷新结果（P-146）：fresh/stale 与批次覆盖率，一眼看"上次是否整批成功"
            "fresh_files": info.get("fresh_files"),
            "stale_files": info.get("stale_files"),
            "refresh": info.get("refresh"),
        }
    return out


def record_refresh(dataset: str, written: int, expected: int, ok: bool) -> None:
    """记录一次**整批刷新**的结果（P-146）：manifest 一眼可见是否整批成功。

    与 `_touch` 的分工：`_touch` 记单文件写入；本函数在整批结束后记批次结果。
    - ok=True：更新 `files`（以盘上真值为准）/`fresh_files`/`stale_files`，
      **不动 `updated_at`**（`_touch` 已写，避免用批次时间覆盖最后写入时间）。
    - ok=False：只记 `refresh`（ok=False + coverage），**不动 `updated_at`/`fresh_files`**——
      失败尝试不得把数据伪装成"刚更新"。
    """
    with cross_process_lock(_manifest_lock_path()):
        with _LOCK:
            m = _read_manifest()
            ds = m["datasets"].setdefault(dataset, {"files": 0, "rows": 0})
            folder = STORE_ROOT / dataset
            disk = len(list(folder.glob("*"))) if folder.exists() else 0
            ds["refresh"] = {
                "mode": "full",
                "ok": bool(ok),
                "written": int(written),
                "expected": int(expected),
                "coverage": round(written / expected, 4) if expected else None,
                "attempted_at": datetime.now().isoformat(timespec="seconds"),
            }
            if ok:
                ds["files"] = disk
                ds["fresh_files"] = int(written)
                ds["stale_files"] = max(0, disk - int(written))
            m["schema_version"] = SCHEMA_VERSION
            _write_manifest(m)


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
    # Δ4：重建也是 manifest RMW，与 _touch 同一把跨进程锁。
    with cross_process_lock(_manifest_lock_path()):
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

# parquet 数据集 schema 白名单（P-124，ADR-0036 D3）：登记数据集的列集必须 ∈ 白名单，
# 白名单外拒写——多路写同一仓的横向契约（单位/排序/列集）收口在写入层，不靠调用方自觉。
# 未登记的数据集（snapshot/events 等）暂不校验，新源接入（T7-e）登记一个收敛一个。
PARQUET_SCHEMA_WHITELIST = {
    "kline": (
        ("date", "open", "high", "low", "close", "volume"),
        ("date", "open", "close", "high", "low", "volume", "amount", "exchange"),
        ("date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"),
    ),
    "kline_raw": (
        ("date", "open", "high", "low", "close", "volume"),
        ("date", "open", "close", "high", "low", "volume", "amount", "exchange"),
        ("date", "open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover"),
    ),
    "index": (
        ("date", "open", "high", "low", "close", "volume"),
    ),
}


def write_frame(dataset: str, key: str, df: pd.DataFrame) -> bool:
    """原子写入一个 DataFrame；空帧不落盘。

    写入收口（P-123/P-124，ADR-0036）：带 date 列的帧统一升序后落盘
    （``read_frame``/``tail()`` 的「末行=最新」语义由此成立）；登记了 schema
    白名单的数据集列集白名单外拒写（返回 False，不落盘）。
    """
    if not enabled() or df is None or getattr(df, "empty", True):
        return False
    allowed = PARQUET_SCHEMA_WHITELIST.get(dataset)
    if allowed is not None:
        cols = tuple(map(str, df.columns))
        if cols not in allowed:
            logger.warning(
                f"[local_store] schema 白名单外拒写 {dataset}/{key}: "
                f"{len(cols)} 列 {list(cols)[:6]}…")
            return False
    if "date" in df.columns:
        try:
            df = df.sort_values("date").reset_index(drop=True)
        except (TypeError, ValueError) as e:
            logger.warning(f"[local_store] date 列排序失败，按原序落盘 {dataset}/{key}: {e}")
    p = _path(dataset, key, ".parquet")
    with _LOCK:
        new_file = not p.exists()
        old_rows = 0 if new_file else _parquet_rows(p)
        _ensure_dir(p)
        # Δ4：tmp 名带 pid+uuid——两进程同写一个 key 时固定 tmp 名互相覆盖。
        # with_name 保留 .parquet 段，末尾 .tmp 仍被 _disk_truth 排除。
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            df.to_parquet(tmp, index=False)
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
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
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
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


def staleness_report(now: Optional[datetime] = None,
                     max_age_days: int = 3) -> list:
    """整仓新鲜度告警面（Δ2，2026-11-22）：晨报消费，只读记账，不触发任何取数。

    口径 = manifest.updated_at（该数据集最后一次**成功写仓**的时间）距今超过
    ``max_age_days`` 即 stale——衡量「同步管道是否在跑」这一运营问题；单文件
    行情日期的新鲜度由读路径 :func:`is_fresh` 按个股处理，两者互补。

    返回 ``[(dataset, status, detail)]``，status ∈ ok / stale / empty / disabled；
    消费方（reporter/scheduler 晨报）对非 ok 项前置告警段。
    """
    if not enabled():
        return [("local_store", "disabled", "QUANT_LOCAL_STORE=off")]
    now = now or datetime.now()
    out = []
    datasets = _read_manifest().get("datasets", {})
    for ds in ("kline", "kline_raw", "index", "snapshot", "events",
               "fundflow", "financial", "blocks"):
        info = datasets.get(ds) or {}
        files = int(info.get("files") or 0)
        if files == 0:
            out.append((ds, "empty", "仓内无文件（未同步过）"))
            continue
        ts = info.get("updated_at")
        if not ts:
            out.append((ds, "stale", "无记账时间"))
            continue
        try:
            age = now - datetime.fromisoformat(ts)
        except ValueError:
            out.append((ds, "stale", f"记账时间不可解析: {ts}"))
            continue
        if age > timedelta(days=max_age_days):
            out.append((ds, "stale", f"{age.days} 天未同步（末次 {ts}）"))
        else:
            out.append((ds, "ok", f"files={files} updated={ts}"))
    return out
