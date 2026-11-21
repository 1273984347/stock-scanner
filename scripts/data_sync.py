"""项目内数据仓同步命令（ADR-0033）。

用法：
    python scripts/data_sync.py --report
    python scripts/data_sync.py --verify                # manifest 账实对账（不取数）
    python scripts/data_sync.py --verify --fix-manifest # 对账并按盘上真值重建 manifest
    python scripts/data_sync.py --content               # 内容判据（P-122/123/124，不取数）
    python scripts/data_sync.py --datasets kline,index,snapshot,events --universe top50 --days 250
    python scripts/data_sync.py --datasets financial,fundflow --universe top20
    python scripts/data_sync.py --datasets kline --universe all --workers 4 --resume
    python scripts/data_sync.py --datasets kline_raw --universe all --workers 4 --resume

设计：
- 只做「取数 → 写本地仓」，不做分析与选股；
- 每只标的失败不影响其它标的，最后汇总成功/失败/跳过；
- 单标的请求间隔 `--sleep`（默认 0.05s），避免把公共网关打到限流；
- `--workers >1` 用线程池并发（2026-11-20 实测 4 并发/16 只零失败，全市场约 50min）；
- `--resume` 断点续跑：进度落 cache/data_sync_progress.json（gitignored），
  按当天日期隔离，中断后重跑跳过已成功标的——这是全市场能跑完的关键。
- kline（前复权，回测栈）与 kline_raw（不复权，默认栈/扫描）分仓同步（T7-g），
  两者口径绝不混写。
"""
import argparse
import json
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PROGRESS = _ROOT / "cache" / "data_sync_progress.json"


def _universe(kind: str, limit: int):
    from lib.data.data_layer import get_realtime_quotes
    df = get_realtime_quotes()
    if df is None or df.empty:
        return []
    if "成交额" in df.columns:
        df = df.sort_values("成交额", ascending=False)
    codes = [str(c).zfill(6) for c in df["代码"].astype(str)]
    if kind == "all":
        return codes[:limit] if limit else codes
    if kind == "top50":
        return codes[:50]
    if kind == "top300":
        return codes[:300]
    if kind.startswith("codes="):
        return kind.split("=", 1)[1].split(",")
    return codes[:50]


# ---------------------------------------------------------------- 断点续跑
def _progress_path(dataset: str = "kline") -> pathlib.Path:
    """kline 沿用旧文件名（兼容已有进度）；其它数据集按名隔离。"""
    if dataset == "kline":
        return _PROGRESS
    return _PROGRESS.with_name(f"data_sync_progress_{dataset}.json")


def _load_progress(today: str, dataset: str = "kline") -> set:
    try:
        d = json.loads(_progress_path(dataset).read_text(encoding="utf-8"))
        if d.get("date") == today:
            return set(d.get("done", []))
    except (ValueError, OSError):
        pass
    return set()


def _save_progress(today: str, done: set, dataset: str = "kline") -> None:
    path = _progress_path(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"date": today, "done": sorted(done)}), encoding="utf-8")
    os.replace(tmp, path)


def sync_kline(codes, days, sleep, workers=1, resume=False, source="auto",
               dataset="kline"):
    """返回 (ok, fail, skipped)；resume 时跳过当天进度文件中已完成的标的。

    dataset="kline"（前复权，回测栈）或 "kline_raw"（不复权，默认栈/扫描）——
    复权口径由 prefer_adjusted 传给取数层，registry 按口径自动写对应仓（C15）。
    source="auto" 走多源栈且自动写本地仓；指定单源（如腾讯被 WAF 拦时用 westock）
    时取数层不写仓（use_local=False），这里显式补写回对应数据集。
    """
    from datetime import date
    from lib.data import local_store
    from lib.data.data_source_registry import get_stock_hist_multi_source, _store_key

    prefer_adjusted = (dataset == "kline")
    today = date.today().isoformat()
    done = _load_progress(today, dataset) if resume else set()
    todo = [c for c in codes if c not in done]
    skipped = len(codes) - len(todo)
    ok = fail = 0

    def pull(code):
        for _ in range(2):  # 单只最多重试 1 次
            try:
                df = get_stock_hist_multi_source(code, days=days,
                                                 prefer_adjusted=prefer_adjusted,
                                                 source=source)
                if not df.empty:
                    if source != "auto":
                        local_store.write_frame(dataset, _store_key(code), df)
                    return True
            except Exception:
                pass
            if sleep:
                time.sleep(sleep)
        return False

    def record(code, success):
        nonlocal ok, fail
        if success:
            ok += 1
            if resume:
                done.add(code)
        else:
            fail += 1

    if skipped:
        print(f"  {dataset} 断点续跑：跳过已完成 {skipped}，待处理 {len(todo)}",
              flush=True)

    if workers <= 1:
        for i, code in enumerate(todo, 1):
            record(code, pull(code))
            if resume and i % 50 == 0:
                _save_progress(today, done, dataset)
            if i % 20 == 0:
                print(f"  {dataset} {i}/{len(todo)} ok={ok} fail={fail}", flush=True)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(pull, c): c for c in todo}
            for i, fut in enumerate(as_completed(futs), 1):
                record(futs[fut], fut.result())
                if resume and i % 50 == 0:
                    _save_progress(today, done, dataset)
                if i % 20 == 0:
                    print(f"  {dataset} {i}/{len(todo)} ok={ok} fail={fail}",
                          flush=True)
    if resume:
        _save_progress(today, done, dataset)
    return ok, fail, skipped


def sync_index(codes, days, sleep):
    from lib.data.data_layer import get_index_hist
    ok = fail = 0
    for code in codes:
        df = get_index_hist(code, days=days)
        ok += 0 if df.empty else 1
        fail += 1 if df.empty else 0
        time.sleep(sleep)
    return ok, fail


def sync_snapshot(sleep):
    from lib.data.data_layer import get_realtime_quotes
    df = get_realtime_quotes(force=True)
    return (1, 0) if df is not None and not df.empty else (0, 1)


def sync_events(sleep):
    from config import get_config
    import lib.strategy.strategies as st
    ok = fail = 0
    for fn in (st.get_zt_candidates, st.get_lhb_candidates):
        df = fn(get_config())
        ok += 0 if df.empty else 1
        fail += 1 if df.empty else 0
    return ok, fail


def sync_blocks(sleep):
    """新浪行业分类 → 全市场「个股→行业」映射落 blocks 仓（ADR-0033）。

    与其它数据集不同：板块映射只能整批拉取（84 个板块 → 成分股反建），无法逐只取，
    因此不走断点骨架——一次成功即整批写仓，失败则不写（保持上次完整快照）。
    """
    from lib.data import local_store
    from lib.data.data_sector import fetch_industry_mapping

    t = time.time()
    mapping = fetch_industry_mapping(sleep=sleep)
    if not mapping:
        print("  blocks 拉取失败/为空，未写仓", flush=True)
        return 0, 1, 0
    ok = 0
    for code, names in mapping.items():
        if local_store.write_json("blocks", code, names):
            ok += 1  # write_json 内部逐只 touch manifest
    print(f"  blocks 落盘 {ok} 只 / {len(mapping)} 映射 {time.time()-t:.1f}s", flush=True)
    return ok, 0, 0


# ---------------------------------------------------------------- 内容判据（P-122/123/124）

# schema 白名单单一真源 = lib.data.local_store.PARQUET_SCHEMA_WHITELIST
# （ADR-0036：判据与写仓契约共用一份，禁止两处漂移）。
# 升序判据覆盖的数据集（P-123）：date 必须单调不减
CONTENT_SORTED_DATASETS = ("kline", "kline_raw", "index")
# volume 交叉校验（P-122）：前复权不改成交量 ⇒ 同日 kline/kline_raw 比值应 ≈1，
# 超出带宽即单位错（×100 实测命中 610 只科创板）
VOLUME_RATIO_BAND = (0.2, 5.0)


def check_content(root=None, max_examples: int = 20) -> int:
    """--content：本地仓三项内容判据（只读盘，不取数）。

    1) 升序判据（P-123）：kline/kline_raw/index 每个文件 date 单调不减；
    2) schema 白名单（P-124）：parquet 列集必须 ∈ PARQUET_SCHEMA_WHITELIST；
    3) volume 交叉校验（P-122）：kline/kline_raw 同日 volume 比值 ∈ VOLUME_RATIO_BAND
       （前复权不改成交量，×100 单位错在此判红）。

    返回 0 = 全绿；1 = 任一判据有违例。2026-11-21 首跑预期判红（存量
    610 只 ×100 / 2825 只降序 / 17 只厂商帧，待 ADR-0036 修复后转绿）。
    """
    import pandas as pd
    from lib.data import local_store

    root = pathlib.Path(root) if root is not None else local_store.STORE_ROOT
    sort_viol = []    # (dataset, code, 顺序形态)
    schema_viol = []  # (dataset, code, 列集描述)
    frames = {}       # dataset -> {code: DataFrame(date 已 normalize, 升序)}

    for ds in CONTENT_SORTED_DATASETS:
        d = root / ds
        if not d.is_dir():
            print(f"  [warn] {ds}: 目录不存在，跳过")
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".parquet"):
                continue
            code = fn[:-len(".parquet")]
            try:
                df = pd.read_parquet(d / fn)
            except Exception as e:
                schema_viol.append((ds, code, f"不可读: {type(e).__name__}"))
                continue
            cols = tuple(map(str, df.columns))
            if cols not in local_store.PARQUET_SCHEMA_WHITELIST.get(ds, ()):
                schema_viol.append((ds, code, f"{len(cols)}列 {list(cols)[:6]}…"))
                continue
            if "date" not in df.columns:
                continue
            dates = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
            diffs = dates.diff().dropna()
            if len(diffs) and (diffs < pd.Timedelta(0)).any():
                order = "desc" if (diffs <= pd.Timedelta(0)).all() else "mixed"
                sort_viol.append((ds, code, order))
            keep = [c for c in ("date", "volume") if c in df.columns]
            frames.setdefault(ds, {})[code] = (
                df[keep].assign(date=dates)
                .dropna(subset=["date"]).sort_values("date"))

    ratio_viol = []  # (code, date, kline_volume, raw_volume, ratio)
    n_pairs = 0
    kl = frames.get("kline") or {}
    kr = frames.get("kline_raw") or {}
    lo, hi = VOLUME_RATIO_BAND
    for code in sorted(set(kl) & set(kr)):
        a, b = kl[code], kr[code]
        if a.empty or b.empty:
            continue
        common = set(a["date"]) & set(b["date"])
        if not common:
            continue
        d = max(common)
        va_s = a.loc[a["date"] == d, "volume"]
        vb_s = b.loc[b["date"] == d, "volume"]
        if va_s.empty or vb_s.empty:
            continue
        va, vb = float(va_s.iloc[-1]), float(vb_s.iloc[-1])
        if vb <= 0:
            continue
        n_pairs += 1
        r = va / vb
        if not (lo <= r <= hi):
            ratio_viol.append((code, str(d)[:10], va, vb, round(r, 3)))

    print("== 本地仓内容判据（--content）==")
    print(f"[{'FAIL' if sort_viol else 'PASS'}] 升序判据（P-123）: 违例 {len(sort_viol)}"
          + (f"  例: {sort_viol[:max_examples]}" if sort_viol else ""))
    print(f"[{'FAIL' if schema_viol else 'PASS'}] schema 白名单（P-124）: 违例 {len(schema_viol)}"
          + (f"  例: {schema_viol[:max_examples]}" if schema_viol else ""))
    print(f"[{'FAIL' if ratio_viol else 'PASS'}] volume 交叉（P-122）: 比对 {n_pairs} 对, "
          f"违例 {len(ratio_viol)}"
          + (f"  例: {ratio_viol[:max_examples]}" if ratio_viol else ""))
    total = len(sort_viol) + len(schema_viol) + len(ratio_viol)
    if total == 0:
        print("[PASS] content: 三项判据全绿")
        return 0
    print(f"[FAIL] content: 共 {total} 条违例（P-122/123/124 存量，修复路径见 ADR-0036）")
    return 1


def _sync_json_dataset(dataset, codes, sleep, resume, fetch, success):
    """financial/fundflow 共用的断点同步骨架：取数层内部已写仓，这里只判成功+记进度。"""
    from datetime import date
    today = date.today().isoformat()
    done = _load_progress(today, dataset) if resume else set()
    todo = [c for c in codes if c not in done]
    skipped = len(codes) - len(todo)
    ok = fail = 0
    if skipped:
        print(f"  {dataset} 断点续跑：跳过已完成 {skipped}，待处理 {len(todo)}", flush=True)
    for i, code in enumerate(todo, 1):
        try:
            good = success(fetch(code))
        except Exception:
            good = False  # 单只失败不影响其它标的（fundflow 会抛 FundFlowUnavailableError）
        if good:
            ok += 1
            if resume:
                done.add(code)
        else:
            fail += 1
        time.sleep(sleep)
        if resume and i % 50 == 0:
            _save_progress(today, done, dataset)
        if i % 20 == 0:
            print(f"  {dataset} {i}/{len(todo)} ok={ok} fail={fail}", flush=True)
    if resume:
        _save_progress(today, done, dataset)
    return ok, fail, skipped


def sync_financial(codes, sleep, resume=False):
    from lib.scoring.data_tdx_fin_adapter import get_stock_fundamentals
    return _sync_json_dataset("financial", codes, sleep, resume,
                              get_stock_fundamentals, lambda r: r is not None)


def sync_fundflow(codes, sleep, resume=False, use_batch=True):
    """全市场资金流：批量层优先（ADR-0018 1 call → 逐码落仓），缺口逐股兜底。

    批量层命中的码直接写仓（与 seed 包同格式 {main_net_ratio, net_inflow_5d}），
    未命中的码（停牌/新股/批量失败）走逐股断点骨架。成功判据放宽为
    ``main_net_ratio is not None``——真实零值也算成功（原判据把合法零值
    误计为 fail，P-114 缺失语义下 None 才是缺失）。
    """
    from lib.data import local_store
    from lib.data.data_layer import get_stock_fund_flow

    batch_ok = 0
    todo = list(codes)
    if use_batch:
        try:
            from lib.data.data_layer import _get_market_fund_flow
            market = _get_market_fund_flow()
        except Exception as e:
            print(f"  fundflow 批量层不可用，整场走逐股: {type(e).__name__}", flush=True)
            market = {}
        if market:
            for code in codes:
                row = market.get(code)
                if row is None:
                    continue
                if local_store.write_json(
                        "fundflow", code,
                        {"main_net_ratio": row["main_net_ratio"],
                         "net_inflow_5d": row["net_inflow_5d"]}):
                    batch_ok += 1
            todo = [c for c in codes if c not in market]
            print(f"  fundflow 批量层落仓 {batch_ok}/{len(codes)}，"
                  f"缺口 {len(todo)} 只走逐股", flush=True)

    ok, fail, skipped = _sync_json_dataset(
        "fundflow", todo, sleep, resume, get_stock_fund_flow,
        lambda r: r is not None and r.get("main_net_ratio") is not None)
    return batch_ok + ok, fail, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--datasets", default="kline,index,snapshot,events",
                    help="逗号分隔：kline,kline_raw,index,snapshot,events,financial,fundflow,blocks")
    ap.add_argument("--universe", default="top50", help="top50/top300/all/codes=600519,000001")
    ap.add_argument("--limit", type=int, default=0, help="all 模式下的上限")
    ap.add_argument("--days", type=int, default=250)
    ap.add_argument("--sleep", type=float, default=0.05)
    ap.add_argument("--workers", type=int, default=1, help="kline/kline_raw 并发数（>1 走线程池）")
    ap.add_argument("--source", default="auto", help="kline 取数源：auto(默认多源栈) 或单源如 westock")
    ap.add_argument("--resume", action="store_true", help="断点续跑（进度落 cache/，按当天隔离）")
    ap.add_argument("--report", action="store_true", help="只打印本地仓覆盖，不取数")
    ap.add_argument("--verify", action="store_true",
                    help="manifest 账实对账（不取数）：manifest 记账 vs 盘上真值")
    ap.add_argument("--content", action="store_true",
                    help="内容判据（不取数）：date 升序 / schema 白名单 / volume 交叉校验"
                         "（P-122/123/124，修复路径见 ADR-0036）")
    ap.add_argument("--fix-manifest", action="store_true",
                    help="配合 --verify：按盘上真值重建 manifest（保留 updated_at/last_key）")
    args = ap.parse_args()

    from lib.data import local_store

    if args.report:
        cov = local_store.coverage()
        if not cov:
            print("本地仓为空：data/store/")
            return 0
        for ds, info in cov.items():
            print(f"{ds:12s} files={info['files']:5d} rows={info['rows']:8d} updated={info['updated_at']}")
        return 0

    if args.verify:
        truth = local_store.rebuild_manifest(dry_run=True)
        # 账侧必须读 manifest 原始 json（coverage().files 从盘上现算，漂移不可见）
        raw = json.loads(local_store.MANIFEST_PATH.read_text(encoding="utf-8"))
        acc_all = raw.get("datasets", {})
        drift = 0
        print(f"{'dataset':12s} {'账(files/rows)':>18s} {'实(files/rows)':>18s} 结论")
        for ds in sorted(set(acc_all) | set(truth)):
            acc = acc_all.get(ds) or {}
            real = truth.get(ds) or {}
            a = (acc.get("files", 0), acc.get("rows", 0))
            r = (real.get("files", 0), real.get("rows", 0))
            same = a == r
            drift += 0 if same else 1
            print(f"{ds:12s} {f'{a[0]}/{a[1]}':>18s} {f'{r[0]}/{r[1]}':>18s} "
                  f"{'OK' if same else 'DRIFT'}")
        if drift == 0:
            print("[PASS] manifest 账实一致")
            return 0
        print(f"[FAIL] {drift} 个数据集账实不符" +
              ("（--fix-manifest 重建）" if not args.fix_manifest else ""))
        if args.fix_manifest:
            local_store.rebuild_manifest()
            print("[OK] manifest 已按盘上真值重建")
            return 0
        return 1

    if args.content:
        return check_content(local_store.STORE_ROOT)

    sets = [s.strip() for s in args.datasets.split(",") if s.strip()]
    codes = _universe(args.universe, args.limit)
    print(f"目标数据集: {sets} | universe={args.universe} ({len(codes)} 只) | days={args.days} "
          f"| workers={args.workers} | source={args.source} | resume={args.resume}", flush=True)

    total_ok = total_fail = total_skip = 0
    for ds in sets:
        t = time.time()
        skip = 0
        if ds == "kline":
            ok, fail, skip = sync_kline(codes, args.days, args.sleep, args.workers,
                                        args.resume, args.source)
            total_skip += skip
        elif ds == "kline_raw":
            ok, fail, skip = sync_kline(codes, args.days, args.sleep, args.workers,
                                        args.resume, args.source, dataset="kline_raw")
            total_skip += skip
        elif ds == "index":
            ok, fail = sync_index(["000300", "000001", "399001"], args.days, args.sleep)
        elif ds == "snapshot":
            ok, fail = sync_snapshot(args.sleep)
        elif ds == "events":
            ok, fail = sync_events(args.sleep)
        elif ds == "financial":
            ok, fail, skip = sync_financial(codes, args.sleep, args.resume)
            total_skip += skip
        elif ds == "fundflow":
            ok, fail, skip = sync_fundflow(codes, args.sleep, args.resume)
            total_skip += skip
        elif ds == "blocks":
            ok, fail, skip = sync_blocks(args.sleep)
        else:
            print(f"未知数据集: {ds}")
            continue
        total_ok += ok; total_fail += fail
        print(f"[{ds}] ok={ok} fail={fail} skip={skip} {time.time()-t:.1f}s", flush=True)

    print(f"\n合计 ok={total_ok} fail={total_fail} skip={total_skip}")
    print("本地仓覆盖：")
    for ds, info in local_store.coverage().items():
        print(f"  {ds:12s} files={info['files']:5d} rows={info['rows']:8d}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
