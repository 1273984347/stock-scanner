#!/usr/bin/env python3
"""补齐 data/store/blocks 缺失股票的行业映射（新股为主）

背景（P-147 修复通道）：新浪行业源（data_sector.fetch_industry_mapping）未收录部分新股，
导致 blocks 本地仓缺文件；sync_blocks 覆盖率校验不会报错，缺口静默存在。

口径（关键，不可混用）：
- 现有 blocks 是**证监会行业分类（2012版）大类**，如"计算机、通信和其他电子设备制造业"。
- 不用申万/同花顺行业（"电子"）——粒度与口径均不同，直接写会污染数据集。
- iFinD `get_stock_info` 的 query `{code}的证监会行业(2012版)大类` 返回同口径值（实测）。
- akshare `stock_industry_change_cninfo` 的「行业大类」为交叉验证源（中国上市公司协会分类）。

硬判据：
1. 取到的行业名必须已存在于现有 blocks 行业名集合（白名单），否则拒绝写入并报告
   ——防止某次接口改口径把新行业名混进数据集。
2. 只补"缺失文件"，已存在的文件不覆盖（幂等，可重复执行）。
3. 写入前打印 dry-run 结果；`--write` 才落盘。

用法：
    py scripts/fill_missing_blocks.py                # dry-run，只列缺口与拟写入值
    py scripts/fill_missing_blocks.py --write        # 实际写入
    py scripts/fill_missing_blocks.py --since 2025-12-01 --write
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
BLOCKS_DIR = ROOT / "data" / "store" / "blocks"
MANIFEST = ROOT / "data" / "store" / "manifest.json"

# 现有 blocks 的行业名集合（白名单）由脚本运行时从磁盘构建，不硬编码


def _ifind_call(params: dict, interval: float = 0.6) -> dict:
    skill = Path(os.environ.get("IFIND_SKILL_DIR",
                                Path.home() / ".workbuddy" / "skills" / "ifind-finance-data"))
    spec = importlib.util.spec_from_file_location("_ifind_call", skill / "call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    time.sleep(interval)
    res = mod.call("stock", "get_stock_info", params)
    if not isinstance(res, dict) or res.get("ok") is False:
        raise RuntimeError(f"get_stock_info failed: {res.get('error') if isinstance(res, dict) else res}")
    return res


def _inner(res: dict) -> dict:
    payload = res.get("data", {}).get("result", {})
    for item in payload.get("content", []):
        try:
            return json.loads(item["text"])["data"]
        except (KeyError, ValueError, TypeError):
            continue
    return {}


def _parse_row(answer: str, code: str) -> dict[str, str]:
    lines = [ln for ln in answer.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3:
        return {}
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        row = dict(zip(header, cells))
        c = next((v for k, v in row.items() if "代码" in k), "")
        if c.split(".")[0] == code:
            return row
    return {}


def _industry_by_ifind(code: str, interval: float) -> str | None:
    """iFinD 取证监会行业(2012版)大类"""
    try:
        data = _inner(_ifind_call({"query": f"{code}的证监会行业(2012版)大类"}, interval))
        row = _parse_row(data.get("answer", ""), code)
        return next((v for k, v in row.items() if "证监会行业" in k and v), None)
    except Exception as exc:  # noqa: BLE001
        print(f"  [ifind] {code} 失败: {exc}", file=sys.stderr)
        return None


def _industry_by_cninfo(code: str) -> str | None:
    """akshare 巨潮：中国上市公司协会行业分类的「行业大类」，交叉验证用"""
    try:
        import akshare as ak
        df = ak.stock_industry_change_cninfo(
            symbol=code, start_date="20250101",
            end_date=datetime.now().strftime("%Y%m%d"))
        if df is None or df.empty or "行业大类" not in df.columns:
            return None
        vals = [str(v).strip() for v in df["行业大类"].tolist() if str(v).strip() not in ("", "nan")]
        return vals[0] if vals else None
    except Exception:  # noqa: BLE001 - 交叉验证源失败不阻断主流程
        return None


def _universe(since: str) -> list[tuple[str, str]]:
    """全市场上市日期 >= since 的股票（沪科/沪主/深/北）"""
    import akshare as ak
    rows: list[tuple[str, str]] = []
    for fn, kw in [("stock_info_sh_name_code", {"symbol": "科创板"}),
                   ("stock_info_sh_name_code", {"symbol": "主板A股"}),
                   ("stock_info_sz_name_code", {"symbol": "A股列表"}),
                   ("stock_info_bj_name_code", {})]:
        try:
            df = getattr(ak, fn)(**kw)
            dc = next(c for c in df.columns if "代码" in c)
            dd = next(c for c in df.columns if "日期" in c)
            for _, row in df.iterrows():
                date = str(row[dd])[:10]
                if date >= since:
                    rows.append((str(row[dc]).zfill(6), date))
        except Exception as exc:  # noqa: BLE001
            print(f"  [universe] {fn} 失败: {exc}", file=sys.stderr)
    dedup: dict[str, str] = {}
    for code, date in rows:
        dedup.setdefault(code, date)
    return sorted(dedup.items(), key=lambda x: x[1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2025-12-01", help="新股上市日期下限")
    ap.add_argument("--codes", default="", help="指定代码，逗号分隔；给了就忽略 --since")
    ap.add_argument("--write", action="store_true", help="实际写入（默认 dry-run）")
    ap.add_argument("--interval", type=float, default=0.6)
    args = ap.parse_args()

    if not BLOCKS_DIR.is_dir():
        raise SystemExit(f"blocks 目录不存在：{BLOCKS_DIR}")

    # 白名单：现有行业名集合
    known: set[str] = set()
    existing: set[str] = set()
    for f in BLOCKS_DIR.glob("*.json"):
        existing.add(f.stem)
        try:
            for name in json.loads(f.read_text(encoding="utf-8")):
                known.add(str(name))
        except (json.JSONDecodeError, TypeError):
            continue
    print(f"现有 blocks {len(existing)} 个文件 / {len(known)} 种行业名")

    if args.codes:
        targets = [(c.strip(), "manual") for c in args.codes.split(",") if c.strip()]
    else:
        targets = _universe(args.since)
    missing = [(c, d) for c, d in targets if c not in existing]
    print(f"待补 {len(missing)} 只（候选 {len(targets)}）")
    if not missing:
        print("无缺口，退出")
        return 0

    results = []
    for i, (code, list_date) in enumerate(missing):
        ifind_val = _industry_by_ifind(code, args.interval)
        cninfo_val = _industry_by_cninfo(code)
        chosen = ifind_val or cninfo_val
        agree = (ifind_val is not None and ifind_val == cninfo_val)
        whitelisted = chosen in known if chosen else False
        results.append({
            "code": code, "list_date": list_date,
            "ifind": ifind_val, "cninfo": cninfo_val,
            "chosen": chosen, "agree": agree, "whitelisted": whitelisted,
            "action": "write" if (chosen and whitelisted) else "skip",
        })
        print(f"  {code} 上市{list_date} ifind={ifind_val} cninfo={cninfo_val} "
              f"{'一致' if agree else '不一致/缺'} -> {chosen or '(空)'}")
        if (i + 1) % 10 == 0:
            print(f"  进度 {i + 1}/{len(missing)}", flush=True)

    writable = [r for r in results if r["action"] == "write"]
    rejected = [r for r in results if r["action"] != "write"]
    print(f"\n可写 {len(writable)} 只 / 拒绝 {len(rejected)} 只")
    for r in rejected:
        reason = "行业名为空" if not r["chosen"] else f"行业名不在白名单: {r['chosen']}"
        print(f"  拒绝 {r['code']}: {reason}")

    if not args.write:
        print("\n[dry-run] 未写入；确认无误后加 --write")
        return 0
    if rejected:
        print("\n存在被拒条目，为防口径污染，本次整体不写入；请先核查上述行业名。")
        return 2

    for r in writable:
        path = BLOCKS_DIR / f"{r['code']}.json"
        path.write_text(json.dumps([r["chosen"]], ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"已写入 {len(writable)} 个文件")
    print(f"写入后 blocks 文件数: {len(list(BLOCKS_DIR.glob('*.json')))}")

    # manifest 同步：files 与 fresh_files 同步 +N（这些文件现在有数据，不应计为 stale）；
    # refresh 快照保持不动 —— 它记录的是上次 sync_blocks 的历史事实，改它等于伪造刷新记录。
    if MANIFEST.is_file():
        mf = json.loads(MANIFEST.read_text(encoding="utf-8"))
        node = mf.get("datasets", {}).get("blocks") if isinstance(mf.get("datasets"), dict) else mf.get("blocks")
        if isinstance(node, dict) and "files" in node:
            actual = len(list(BLOCKS_DIR.glob("*.json")))
            old_files = node["files"]
            node["files"] = actual
            node["rows"] = actual
            if isinstance(node.get("fresh_files"), int):
                node["fresh_files"] = node["fresh_files"] + len(writable)
            if isinstance(node.get("stale_files"), int):
                node["stale_files"] = max(0, node["files"] - node.get("fresh_files", node["files"]))
            MANIFEST.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"manifest blocks.files: {old_files} -> {node['files']} "
                  f"(fresh={node.get('fresh_files')} stale={node.get('stale_files')}; refresh 快照未动)")
        else:
            print("manifest 结构未识别 blocks.files，未改（请人工核对）")

    # 补写清单留证（可追溯哪些不是 sync 写入的）
    report = ROOT / "cache" / f"blocks_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "written": len(writable),
        "source_primary": "ifind get_stock_info 证监会行业(2012版)大类",
        "source_crosscheck": "akshare stock_industry_change_cninfo 行业大类",
        "items": writable,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"补写清单: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
