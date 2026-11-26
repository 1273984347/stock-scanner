"""从整合数据生成两套用途数据集：回测可用 + 前端展示。

步骤：
1. 清洗 K线 OHLC=0 占位行（港 21.3% / 美 11.4% / A股 0.03%）→ _clean/
2. 回测集 _usecases/backtest/：严格口径分层
   - A股真前复权（hs_kline_qfq_500）→ 可直接回测
   - 港美股清洗后不复权 K线 → 标注需复权，仅供研究/展示，不默认回测
   - 财务/资金流/北向/技术指标（因子层）
3. 前端集 _usecases/frontend/：行情/清洗K线/新闻研报公告/榜单

用法：python scripts/prepare_usecases.py
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
BULK = ROOT / "data" / "exports" / "bulk"
INTG = BULK / "_integrated"
EXTRA = BULK / "_extra"
CLEAN = BULK / "_clean"
BT = BULK / "_usecases" / "backtest"
FE = BULK / "_usecases" / "frontend"
for d in (CLEAN, BT, FE):
    d.mkdir(parents=True, exist_ok=True)


def clean_kline(market: str):
    """剔除 open/high/low=0 的占位行（保留 last/close 有效的行也不用于回测）。"""
    src = INTG / f"{market}_kline.parquet"
    df = pd.read_parquet(src)
    n0 = len(df)
    o, h, l = (pd.to_numeric(df[k], errors="coerce") for k in ("open", "high", "low"))
    bad = (o <= 0) | (h <= 0) | (l <= 0)
    cleaned = df.loc[~bad].copy()
    cleaned.to_parquet(CLEAN / f"{market}_kline_clean.parquet", index=False)
    report = {
        "market": market, "rows_before": n0, "rows_removed": int(bad.sum()),
        "rows_after": len(cleaned), "removed_pct": round(bad.mean() * 100, 4),
        "codes_before": df["code"].nunique() if "code" in df else 0,
        "codes_after": cleaned["code"].nunique() if "code" in cleaned else 0,
    }
    return report, cleaned


def main():
    reports = []
    print("=== 1. 清洗 K线 OHLC=0 ===")
    for m in ("hs", "hk", "us"):
        rep, _ = clean_kline(m)
        reports.append(rep)
        print(f"  {m}: {rep['rows_before']:,} -> {rep['rows_after']:,} "
              f"(剔除 {rep['rows_removed']:,} / {rep['removed_pct']}%)")
    pd.DataFrame(reports).to_csv(CLEAN / "clean_report.csv", index=False, encoding="utf-8-sig")

    print("\n=== 2. 回测集 ===")
    bt_notes = []
    # 2a. A股真前复权（可直接回测）—— 直接从增量 jsonl 实时读取
    # （_integrated/hs_kline_qfq_500.parquet 是整合时的旧快照，不会自动更新）
    import json
    qfq_log = BULK / "_qfq" / "hs_qfq.jsonl"
    if qfq_log.exists():
        rows = []
        for line in qfq_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        o, h, l = (pd.to_numeric(df[k], errors="coerce") for k in ("open", "high", "low"))
        bad = (o <= 0) | (h <= 0) | (l <= 0)
        df = df.loc[~bad].copy()
        df.to_parquet(BT / "hs_kline_qfq.parquet", index=False)
        bt_notes.append(f"- `hs_kline_qfq.parquet`：**真前复权**，{len(df):,} 行 / {df['code'].nunique():,} 只，可直接回测（增量批次，未覆盖全部 5580 只）")
        print(f"  A股真前复权: {len(df):,} 行 / {df['code'].nunique()} 只")
    # 2b. 港美股清洗后不复权（标注）
    for m in ("hk", "us"):
        src = CLEAN / f"{m}_kline_clean.parquet"
        df = pd.read_parquet(src)
        df.to_parquet(BT / f"{m}_kline_raw_clean.parquet", index=False)
        bt_notes.append(f"- `{m}_kline_raw_clean.parquet`：**不复权**（已剔 OHLC=0），{len(df):,} 行，跨除权日有跳空，需先复权再回测，默认仅供研究")
        print(f"  {m} 清洗不复权: {len(df):,} 行")
    # 2c. A股全量清洗后不复权（前复权未覆盖的票）
    hs = pd.read_parquet(CLEAN / "hs_kline_clean.parquet")
    hs.to_parquet(BT / "hs_kline_raw_clean.parquet", index=False)
    bt_notes.append(f"- `hs_kline_raw_clean.parquet`：**不复权**（已剔 OHLC=0），{len(hs):,} 行，前复权未覆盖的票需先复权")
    # 2d. 因子层
    for name, src in [
        ("hs_finance.parquet", INTG / "hs_finance.parquet"),
        ("hk_finance.parquet", INTG / "hk_finance.parquet"),
        ("us_finance.parquet", INTG / "us_finance.parquet"),
        ("fund_all.parquet", EXTRA / "fund_all.parquet"),
        ("north_all.parquet", EXTRA / "north_all.parquet"),
        ("hs_technical.parquet", INTG / "hs_technical.parquet"),
    ]:
        if src.exists():
            (BT / name).write_bytes(src.read_bytes())
    (BT / "README.md").write_text(
        "# 回测可用数据集\n\n> 口径声明（必读）：\n" + "\n".join(bt_notes) + "\n\n"
        "因子层：finance(三市场) / fund_all / north_all / hs_technical 已复制。\n"
        "使用前必须确认复权口径——不复权 K线跨除权日有跳空，直接回测会产生虚假收益。\n",
        encoding="utf-8")

    print("\n=== 3. 前端集 ===")
    fe_files = []
    for m in ("hs", "hk", "us"):
        src = INTG / f"{m}_quote.parquet"
        if src.exists():
            (FE / src.name).write_bytes(src.read_bytes()); fe_files.append(src.name)
        src = CLEAN / f"{m}_kline_clean.parquet"
        if src.exists():
            (FE / f"{m}_kline.parquet").write_bytes(src.read_bytes()); fe_files.append(f"{m}_kline.parquet")
    for name in ["news_all.parquet", "report_all.parquet", "notice_all.parquet",
                 "sector_ranking_industry.parquet", "sector_ranking_concept.parquet",
                 "index_constituent_hs300.parquet", "index_constituent_zz500.parquet",
                 "index_constituent_sz50.parquet", "macro_cn_core.parquet",
                 "hot_stock.parquet", "hot_news.parquet", "hot_etf.parquet", "hot_sector.parquet",
                 "fund_all.parquet", "north_all.parquet", "south_all.parquet"]:
        src = EXTRA / name
        if src.exists():
            (FE / name).write_bytes(src.read_bytes()); fe_files.append(name)
    (FE / "README.md").write_text(
        f"# 前端展示数据集\n\n共 {len(fe_files)} 个 parquet：\n"
        + "\n".join(f"- `{f}`" for f in sorted(fe_files))
        + "\n\nK线已剔除 OHLC=0 占位行；行情为快照。展示用不复权价即可。\n",
        encoding="utf-8")
    print(f"  前端 {len(fe_files)} 个文件")
    print("\n完成：_clean/ + _usecases/backtest/ + _usecases/frontend/")


if __name__ == "__main__":
    main()
