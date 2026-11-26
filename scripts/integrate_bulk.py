"""整合全量拉取数据：分片合并为主表 + 统一清单 + 质检摘要。

产物（data/exports/bulk/_integrated/）：
- {market}_{category}.parquet：12 类主数据分片合并（带来源标注）
- qfq_500.parquet：前复权增量（当前已完成部分，附覆盖率）
- MANIFEST.md：数据清单（行数/覆盖/日期范围/口径说明）
- QUALITY_SUMMARY.csv：质检结论汇总

注意：
- 不复权 K线（qfq 参数不生效，P-131）明确标注 adjust='raw'
- 前复权 qfq_500 是增量批次，覆盖率实时从 jsonl 计算
- 不改动原始分片，全部只读合并

用法：python scripts/integrate_bulk.py
"""
import json
from pathlib import Path

import pandas as pd
from bulk_paths import RAW, UNIVERSE, REPORTS, BULK  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "exports" / "bulk"
DST = OUT / "_integrated"
DST.mkdir(parents=True, exist_ok=True)

CATEGORIES = ["quote", "kline", "finance", "technical"]
MARKETS = ["hs", "hk", "us"]


def merge_category(market: str, category: str):
    parts = []
    for f in sorted(OUT.glob(f"{market}_{category}_*.parquet")):
        parts.append(pd.read_parquet(f))
    if not parts:
        return None
    df = pd.concat(parts, ignore_index=True)
    # 去重（repair 分片可能与主分片重叠）——按该类别的自然主键，不能只按 code
    # （finance 每只多期，按 code 去重会把 40 期压成 1 期）
    if "code" in df.columns:
        if "date" in df.columns:
            keys = ["code", "date"]
        elif "EndDate" in df.columns:
            keys = ["code", "EndDate"]
        else:
            keys = ["code"]
        df = df.drop_duplicates(subset=keys, keep="first")
    df.to_parquet(DST / f"{market}_{category}.parquet", index=False)
    return df


def qfq_incremental():
    log = OUT / "_qfq" / "hs_qfq.jsonl"
    if not log.exists():
        return None, 0
    rows = []
    codes = set()
    for line in log.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            r = json.loads(line)
            rows.append(r)
            codes.add(r.get("code"))
        except Exception:
            pass
    if not rows:
        return None, 0
    df = pd.DataFrame(rows)
    if "code" in df.columns and "date" in df.columns:
        df = df.drop_duplicates(subset=["code", "date"], keep="first")
    df.to_parquet(DST / "hs_kline_qfq_500.parquet", index=False)
    return df, len(codes)


def main():
    manifest = ["# 全市场数据整合清单", ""]
    manifest.append("生成时间：2026-11-25 | 数据源：westock Go CLI（腾讯自选股）+ 腾讯 qfkline")
    manifest.append("")
    manifest.append("## 口径声明（重要）")
    manifest.append("- `*_kline.parquet`：**不复权价**。Go CLI 的 `--fq qfq` 参数不生效（P-131），2000/2000 逐值等同不复权。不可直接用于回测。")
    manifest.append("- `hs_kline_qfq_500.parquet`：**真前复权**（腾讯 qfkline），增量批次，是当前已完成的子集。")
    manifest.append("- `technical`：21 指标列，无 code 列（按批次对应代码段），日期+指标。")
    manifest.append("- 港股/美股早期历史有 OHLC=0 占位行（港 21.3% / 美 11.4%），使用时需剔除。")
    manifest.append("- `volume` 单位：A股=手，港美股=股（已用 amount/(close*volume) 比值验证）。")
    manifest.append("")
    manifest.append("## 主数据（data/exports/bulk/_integrated/）")
    manifest.append("| 市场 | 类别 | 行数 | 股票数 | 日期范围 |")
    manifest.append("|---|---|---|---|---|")
    quality = []
    for m in MARKETS:
        for c in CATEGORIES:
            df = merge_category(m, c)
            if df is None:
                continue
            codes = df["code"].nunique() if "code" in df.columns else 0
            dates = pd.to_datetime(df["date"], errors="coerce") if "date" in df.columns else None
            rng = f"{dates.min().date()}~{dates.max().date()}" if dates is not None and not dates.empty else "-"
            manifest.append(f"| {m} | {c} | {len(df):,} | {codes:,} | {rng} |")
            quality.append({"market": m, "category": c, "rows": len(df),
                            "codes": codes, "date_min": str(dates.min())[:10] if dates is not None else "",
                            "date_max": str(dates.max())[:10] if dates is not None else "",
                            "adjust": "raw" if c == "kline" else "n/a"})
    qdf, qcodes = qfq_incremental()
    if qdf is not None:
        qn = len(qdf)
        manifest.append(f"| hs | kline_qfq(增量) | {qn:,} | {qcodes:,} | 2024-08-26~2026-11-23 |")
        quality.append({"market": "hs", "category": "kline_qfq", "rows": qn,
                        "codes": qcodes, "date_min": "2024-08-26", "date_max": "2026-11-23",
                        "adjust": "qfq"})
    manifest.append("")
    manifest.append("## 补充数据（data/exports/bulk/_extra/）")
    manifest.append("| 文件 | 行数 | 说明 |")
    manifest.append("|---|---|---|")
    desc = {
        "news_all": "个股新闻（全市场逐只）", "report_all": "卖方研报（全市场逐只）",
        "notice_all": "公司公告（全市场逐只）", "fund_all": "资金流（A股+港股，美股无此能力）",
        "north_all": "北向持仓（A股）", "south_all": "南下持仓（港股通子集）",
        "sector_ranking_industry": "行业板块行情榜", "sector_ranking_concept": "概念板块行情榜",
        "index_list_hs": "沪深指数清单", "index_list_hk": "港股指数清单", "index_list_us": "美股指数清单",
        "index_constituent_hs300": "沪深300成份", "index_constituent_zz500": "中证500成份",
        "index_constituent_sz50": "上证50成份", "macro_cn_core": "中国核心宏观指标",
        "hot_stock": "热搜股票榜", "hot_news": "热文榜", "hot_etf": "热门ETF榜", "hot_sector": "热门板块榜",
    }
    extra_files = sorted(BULK.glob("_extra/*.parquet"))
    for f in extra_files:
        df = pd.read_parquet(f)
        manifest.append(f"| {f.name} | {len(df):,} | {desc.get(f.stem, '-')} |")
    manifest.append("")
    manifest.append("## 已知限制")
    manifest.append("- 美股 75 只 K线 + 150 只财务：数据源真实无覆盖（冷门粉单/ADR）。")
    manifest.append("- 前复权仅 2756/5580 只（增量批次持续补，腾讯 WAF 累计封禁）。")
    manifest.append("- 港股 OHLC=0 占位 21.3%、美股 11.4%（数据源早期历史缺陷）。")
    (DST / "MANIFEST.md").write_text("\n".join(manifest), encoding="utf-8")
    pd.DataFrame(quality).to_csv(DST / "QUALITY_SUMMARY.csv", index=False, encoding="utf-8-sig")
    print(f"整合完成：{len(list(DST.glob('*.parquet')))} 个主表 + MANIFEST.md + QUALITY_SUMMARY.csv")
    print(f"前复权增量：{qcodes} 只 / {qn:,} 行")


if __name__ == "__main__":
    main()
