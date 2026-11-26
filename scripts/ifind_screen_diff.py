#!/usr/bin/env python3
"""iFinD 智能选股对拍 / 结构化指标取数工具。

设计原则（对齐项目 AGENTS.md）：
1. 不重复造 HTTP 轮子：动态加载 skill 自带的 call.py，复用其 MCP 会话与工具白名单校验。
2. 密钥不进仓库：只从 skill 的 mcp_config.json 读取；占位符密钥直接拒绝并给出获取地址。
3. 限流：iFinD 免费版 2 QPS，默认每次调用间隔 0.6s；个人版/企业版可用 --interval 调小。
4. 可复用：所有取数逻辑是纯函数，CLI 只是薄封装；对拍条件内置且可 --cases-file 覆盖。
5. 输出落 cache/ifind/（gitignored），不污染版本库。

子命令：
    probe   验证密钥与工具可用性（list_tools 不消耗权益次数）
    screen  跑内置或自定义智能选股条件，落盘候选池
    diff    跑智能选股并与本地基准股票池对拍（Jaccard / 交集 / 差集）
    validate 按项目 stock_pool.py 口径校验 iFinD 选股结果自洽性（不依赖本地 TDX 数据）
    esg     ESG 评级取数（可多个主体，<=5）
    risk    风险定量指标取数（可多个主体，<=5）

用法示例：
    py scripts/ifind_screen_diff.py probe
    py scripts/ifind_screen_diff.py screen --case value_low_pe
    py scripts/ifind_screen_diff.py diff --case value_low_pe --baseline-codes 000858,600519
    py scripts/ifind_screen_diff.py esg --query "诚意药业中诚信ESG评级"
    py scripts/ifind_screen_diff.py risk --query "航天电子在2026-03-19的夏普比率"

ESG / 风险指标取数方法（get_esg_data / get_risk_indicators）：
1. 两者均为自然语言取数，query = "主体 + 指标"（风险指标可再加时间），
   例："诚意药业中诚信ESG评级"、"航天电子在2026-03-19的夏普比率"。
2. 主体支持多主体合并，遵循 skill 通用上限：主体数 / 指标数各 <= 5，
   例："同花顺、东方财富、大智慧、恒生电子的2025-09-30的净利润增速、ROE、ROA"。
3. 返回的是结构化指标值（非公告原文），与 get_stock_events 同性质；
   具体可用指标名以密钥权益下的 list_tools 为准——配好密钥后先跑 probe。
4. 口径定位：这两类是"时点/横截面指标"，不是日频序列，
   应接入因子体系（factors.py / lib/scoring/）而非 K 线源栈。

实测返回结构（2026-11-26 验证通过，可直接复用 parse_security_table 解析）：
- ESG 示例 "诚意药业、贵州茅台的ESG评级" -> 表格列：证券代码 | 证券简称 | 华证ESG评级；
  评级值域含 AAA / B 等等级；响应带 indicators_params 标注"截至日期=最新"。
- 风险示例 "贵州茅台在2026-11-25的夏普比率、最大回撤、波动率" ->
  表格列：证券代码 | 证券简称 | Sharpe | 区间最大回撤(单位:%) | 波动率(单位:%)；
  indicators_params 给出可追溯口径：Sharpe 无风险利率=一年期国债、周期=日、
  区间=截止日52周前至截止日；波动率周期=周；最大回撤复权方式=不复权。
  => 接入因子时必须把 indicators_params 作为元数据落库，否则口径不可复现。

iFinD 选股返回结构（2026-11-26 实测）：
- search_stocks 返回 JSON 字符串：data.answer 是 markdown 表格，
  data.selectedSecuritiesCount = 命中总数，表格仅返回前 100 行（momentum 类为 50）。
- 表格自带指标列（如 PE/PB/股息率），可本地复核条件是否自洽（validate 子命令）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = Path(os.environ.get(
    "IFIND_SKILL_DIR",
    r"C:\Users\12739\.workbuddy\skills\ifind-finance-data",
))
CALL_PY = SKILL_DIR / "call.py"
CONFIG_JSON = SKILL_DIR / "mcp_config.json"
OUT_DIR = ROOT / "cache" / "ifind"
PLACEHOLDER = "your ifind-mcp key"
KEY_URL = "https://mcp.51ifind.com/?cid=c1_UGhY#/profile"

# 内置对拍条件：映射项目 config/_presets.py 与 lib/strategy/stock_pool.py 的现有选股口径。
# 每条 case 的 local_hint 说明它对应项目里哪一段逻辑，便于人工核对是否同条件。
DEFAULT_CASES: dict[str, dict[str, str]] = {
    "value_low_pe": {
        "query": "市盈率5到30倍、市净率0.5到8倍、股息率大于2%的A股股票",
        "local_hint": "stock_pool.py PoolConfig: PE[5,30] PB[0.5,8] dividend>=2% (冷门优质股)",
    },
    "big_cap_tech": {
        "query": "电子行业总市值大于100亿的A股股票",
        "local_hint": "iFinD 文档样例条件；项目无完全对应策略，用于验证行业+市值过滤口径",
    },
    "momentum": {
        "query": "今日涨幅大于3%且成交额排名前50的A股股票",
        "local_hint": "strategies.py get_multi_factor_candidates: 涨跌幅>0 + 成交额前50",
    },
}

CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
EXCHANGE_SUFFIX_RE = re.compile(r"\.(SZ|SH|BJ|OF|IB|HK|US)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def load_ifind_call():
    """动态加载 skill 的 call.py；复用其 MCP 会话管理与工具白名单校验。"""
    if not CALL_PY.is_file():
        raise RuntimeError(f"未找到 iFinD 调用脚本：{CALL_PY}（可用 IFIND_SKILL_DIR 覆盖）")
    spec = importlib.util.spec_from_file_location("ifind_call", CALL_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_key() -> str:
    """读取密钥；占位符或缺失时拒绝执行，避免把无效请求浪费在限流额度上。"""
    if not CONFIG_JSON.is_file():
        raise RuntimeError(f"未找到密钥配置：{CONFIG_JSON}")
    cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    token = str(cfg.get("auth_token", "")).strip()
    if not token or token == PLACEHOLDER:
        raise RuntimeError(
            "iFinD 密钥未配置。请到 MCP 官网个人中心获取密钥并写入 "
            f"{CONFIG_JSON} 的 auth_token 字段：{KEY_URL}"
        )
    return token


def call_tool(server_type: str, tool_name: str, params: dict[str, Any], *, interval: float = 0.6):
    """单次工具调用 + 限流 + 统一错误传播。"""
    check_key()
    mod = load_ifind_call()
    time.sleep(interval)
    res = mod.call(server_type, tool_name, params)
    if isinstance(res, dict) and res.get("ok") is False:
        raise RuntimeError(f"iFinD 调用失败：{res.get('error')}")
    return res


def extract_texts(result: dict[str, Any]) -> list[str]:
    """从 MCP 响应中提取正文。兼容 result.content[].text 与扁平 text 两种形态。"""
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        return []
    payload = data.get("result", data)
    if not isinstance(payload, dict):
        return []
    contents = payload.get("content")
    texts: list[str] = []
    if isinstance(contents, list):
        for item in contents:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                texts.append(item["text"])
    if not texts and isinstance(payload.get("text"), str):
        texts.append(payload["text"])
    return texts


def extract_codes(texts: list[str]) -> list[str]:
    """从 iFinD 返回中抽取股票代码，去重保序。

    根因：iFinD 的 data.answer 是 markdown 表格，股票代码在"股票代码"列（如 603351.SH），
    而股息率等数值列同样含 6 位数字（如 2.305918）。若对整个文本裸扫 \\d{6}，
    会把数值的小数部分误当代码（305918 / 814004 等）。
    正解：先展开 JSON -> data.answer，再按表格表头定位"股票代码"列逐行取；
    只有表格不可解析时才回退到"必须带交易所后缀"的正则，避免误抓数值。
    """
    seen: set[str] = set()
    codes: list[str] = []

    def add(raw: str) -> None:
        m = re.match(r"^(\d{6})", raw.strip())
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            codes.append(m.group(1))

    expanded: list[str] = []
    for text in texts:
        parsed = None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        if isinstance(parsed, dict):
            data = parsed.get("data")
            if isinstance(data, dict) and isinstance(data.get("answer"), str):
                expanded.append(data["answer"])
        expanded.append(text)

    for text in expanded:
        lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
        if not lines:
            continue
        header = [c.strip() for c in lines[0].strip("|").split("|")]
        idx = next((i for i, c in enumerate(header) if "代码" in c), None)
        if idx is None:
            continue
        for line in lines[2:]:  # 第 2 行是 |---|---| 分隔行
            cells = [c.strip() for c in line.strip("|").split("|")]
            if idx < len(cells):
                add(cells[idx])
        if codes:
            return codes

    joined = "\n".join(expanded)
    for code in re.findall(r"(?<!\d)(\d{6})\.(?:SH|SZ|BJ|OF)(?!\w)", joined, re.IGNORECASE):
        add(code)
    return codes


def parse_security_table(texts: list[str]) -> list[dict[str, str]]:
    """解析 data.answer 的 markdown 表格为 dict 列表（列名 -> 值）。

    与 extract_codes 共用"按表头定位"的根因修法，不裸扫数字。
    """
    rows: list[dict[str, str]] = []
    for text in texts:
        expanded = text
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
                answer = parsed["data"].get("answer")
                if isinstance(answer, str):
                    expanded = answer
        except (json.JSONDecodeError, TypeError):
            pass
        lines = [ln for ln in expanded.splitlines() if ln.strip().startswith("|")]
        if len(lines) < 3:
            continue
        header = [c.strip() for c in lines[0].strip("|").split("|")]
        for line in lines[2:]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == len(header):
                rows.append(dict(zip(header, cells)))
        if rows:
            break
    return rows


def extract_answer_meta(texts: list[str]) -> dict[str, Any]:
    """提取 iFinD 选股的元信息（命中总数），用于对拍口径核对。"""
    for text in texts:
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("data"), dict):
            data = parsed["data"]
            return {
                "code": parsed.get("code"),
                "msg": parsed.get("msg"),
                "selected_securities_count": data.get("selectedSecuritiesCount"),
                "data_total_volume": data.get("dataTotalVolume"),
            }
    return {}


def load_cases(path: str | None) -> dict[str, dict[str, str]]:
    if not path:
        return DEFAULT_CASES
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not all(isinstance(v, dict) and "query" in v for v in raw.values()):
        raise ValueError("cases-file 必须是 {case_name: {query: str, ...}} 结构的 JSON")
    return raw


def parse_codes_arg(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = re.split(r"[,\s]+", raw.strip())
    return [p for p in parts if re.fullmatch(r"\d{6}", p)]


def save_json(name: str, payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------
def cmd_probe(args: argparse.Namespace) -> int:
    check_key()
    mod = load_ifind_call()
    res = mod.list_tools("stock")
    tools = (res.get("data", {}).get("result", {}).get("tools", [])) if res.get("ok") else []
    names = sorted(t.get("name", "") for t in tools if isinstance(t, dict))
    print(f"密钥有效。stock 服务可用工具 {len(names)} 个：")
    for name in names:
        print(f"  - {name}")
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases_file)
    if args.case and args.case not in cases:
        raise SystemExit(f"未知 case：{args.case}；可选：{', '.join(cases)}")
    targets = [args.case] if args.case else list(cases)
    results = []
    for name in targets:
        case = cases[name]
        res = call_tool("stock", "search_stocks", {"query": case["query"]}, interval=args.interval)
        texts = extract_texts(res)
        codes = extract_codes(texts)
        meta = extract_answer_meta(texts)
        item = {
            "case": name,
            "query": case["query"],
            "local_hint": case.get("local_hint", ""),
            "selected_securities_count": meta.get("selected_securities_count"),
            "parsed_code_count": len(codes),
            "codes": codes,
            "raw_text": "\n".join(texts),
        }
        results.append(item)
        total = meta.get("selected_securities_count")
        suffix = f"（iFinD 报告总数 {total}，表格返回 {len(codes)}）" if total else ""
        print(f"[{name}] 解析 {len(codes)} 只{suffix}：{'、'.join(codes[:20]) or '(未解析出代码)'}")
    payload = {"generated_at": datetime.now().isoformat(timespec="seconds"), "results": results}
    print(f"已落盘：{save_json('screen', payload)}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    cases = load_cases(args.cases_file)
    if args.case not in cases:
        raise SystemExit(f"未知 case：{args.case}；可选：{', '.join(cases)}")
    case = cases[args.case]
    baseline = set(parse_codes_arg(args.baseline_codes))
    if args.baseline_file:
        raw = Path(args.baseline_file).read_text(encoding="utf-8")
        baseline.update(extract_codes([raw]))
    if not baseline:
        raise SystemExit("必须提供 --baseline-codes 或 --baseline-file 作为本地候选池")

    res = call_tool("stock", "search_stocks", {"query": case["query"]}, interval=args.interval)
    texts = extract_texts(res)
    ifind = set(extract_codes(texts))
    meta = extract_answer_meta(texts)
    inter = ifind & baseline
    union = ifind | baseline
    jaccard = len(inter) / len(union) if union else 0.0
    coverage = len(inter) / len(baseline) if baseline else 0.0

    print(f"case        : {args.case}")
    print(f"query       : {case['query']}")
    print(f"local_hint  : {case.get('local_hint', '')}")
    print(f"ifind 命中  : {len(ifind)} 只")
    print(f"baseline    : {len(baseline)} 只")
    print(f"交集        : {len(inter)} 只 -> {'、'.join(sorted(inter)) or '(空)'}")
    print(f"Jaccard     : {jaccard:.3f}")
    print(f"baseline覆盖: {coverage:.3f}  (交集/baseline)")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "case": args.case,
        "query": case["query"],
        "local_hint": case.get("local_hint", ""),
        "selected_securities_count": meta.get("selected_securities_count"),
        "ifind_codes": sorted(ifind),
        "baseline_codes": sorted(baseline),
        "intersection": sorted(inter),
        "ifind_only": sorted(ifind - baseline),
        "baseline_only": sorted(baseline - ifind),
        "jaccard": round(jaccard, 6),
        "baseline_coverage": round(coverage, 6),
        "raw_text": "\n".join(texts),
    }
    print(f"已落盘：{save_json(f'diff_{args.case}', payload)}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """按项目 stock_pool.py 的 value_low_pe 口径校验 iFinD 选股结果自洽性。

    这是"数据质量对拍"：iFinD 返回的表格自带 PE/PB/股息率，
    用项目硬约束 PE∈[5,30] / PB∈[0.5,8] / 股息率>=2% 逐行复核，
    找出不满足条件的混入项。不依赖项目本地 TDX 数据源（当前已失效）。
    """
    cases = load_cases(args.cases_file)
    case = cases[args.case]
    res = call_tool("stock", "search_stocks", {"query": case["query"]}, interval=args.interval)
    texts = extract_texts(res)
    rows = parse_security_table(texts)
    if not rows:
        raise RuntimeError("未解析到表格，无法校验")

    col = {name: next((k for k in rows[0] if name in k), None) for name in ("代码", "市盈率", "市净率", "股息率")}
    if any(v is None for v in col.values()):
        raise RuntimeError(f"表格列名不匹配，实际列名：{list(rows[0])}")

    def to_num(value: str) -> float | None:
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None

    violations = []
    for row in rows:
        code = row[col["代码"]]
        pe, pb, dv = (to_num(row[col[k]]) for k in ("市盈率", "市净率", "股息率"))
        reasons = []
        if pe is not None and not (5 <= pe <= 30):
            reasons.append(f"PE={pe}")
        if pb is not None and not (0.5 <= pb <= 8):
            reasons.append(f"PB={pb}")
        if dv is not None and dv < 2:
            reasons.append(f"股息率={dv}")
        if reasons:
            violations.append({"code": code, "reasons": reasons})

    meta = extract_answer_meta(texts)
    print(f"case              : {args.case}")
    print(f"iFinD 报告命中总数 : {meta.get('selected_securities_count')}")
    print(f"表格返回行数      : {len(rows)}")
    print(f"违反项目口径      : {len(violations)} 项")
    for v in violations[:20]:
        print(f"  {v['code']}: {', '.join(v['reasons'])}")
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "case": args.case,
        "query": case["query"],
        "constraint": "PE[5,30] PB[0.5,8] dividend_yield>=2%",
        "selected_securities_count": meta.get("selected_securities_count"),
        "table_rows": len(rows),
        "violations": violations,
    }
    print(f"已落盘：{save_json(f'validate_{args.case}', payload)}")
    return 0


def cmd_indicator(args: argparse.Namespace, tool: str) -> int:
    res = call_tool("stock", tool, {"query": args.query}, interval=args.interval)
    texts = extract_texts(res)
    print("\n".join(texts) if texts else json.dumps(res, ensure_ascii=False, indent=2))
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "query": args.query,
        "text": "\n".join(texts),
        "raw": res,
    }
    print(f"已落盘：{save_json(tool, payload)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="验证密钥与工具可用性")
    p_probe.set_defaults(func=cmd_probe)

    p_screen = sub.add_parser("screen", help="跑智能选股条件，落盘候选池")
    p_screen.add_argument("--case", help="条件名；不传则跑全部")
    p_screen.add_argument("--cases-file", help="自定义条件 JSON 文件")
    p_screen.add_argument("--interval", type=float, default=0.6, help="调用间隔秒（免费版>=0.6）")
    p_screen.set_defaults(func=cmd_screen)

    p_diff = sub.add_parser("diff", help="智能选股 vs 本地基准池对拍")
    p_diff.add_argument("--case", required=True, help="条件名")
    p_diff.add_argument("--cases-file", help="自定义条件 JSON 文件")
    p_diff.add_argument("--baseline-codes", help="本地候选池，逗号分隔的 6 位代码")
    p_diff.add_argument("--baseline-file", help="本地候选池文件（从中抽取 6 位代码）")
    p_diff.add_argument("--interval", type=float, default=0.6)
    p_diff.set_defaults(func=cmd_diff)

    p_val = sub.add_parser("validate", help="按项目 value_low_pe 口径校验 iFinD 选股结果自洽性")
    p_val.add_argument("--case", default="value_low_pe")
    p_val.add_argument("--cases-file", help="自定义条件 JSON 文件")
    p_val.add_argument("--interval", type=float, default=0.6)
    p_val.set_defaults(func=cmd_validate)

    p_esg = sub.add_parser("esg", help="ESG 评级取数；query 如 '诚意药业中诚信ESG评级'")
    p_esg.add_argument("--query", required=True)
    p_esg.add_argument("--interval", type=float, default=0.6)
    p_esg.set_defaults(func=lambda a: cmd_indicator(a, "get_esg_data"))

    p_risk = sub.add_parser("risk", help="风险指标取数；query 如 '航天电子在2026-03-19的夏普比率'")
    p_risk.add_argument("--query", required=True)
    p_risk.add_argument("--interval", type=float, default=0.6)
    p_risk.set_defaults(func=lambda a: cmd_indicator(a, "get_risk_indicators"))

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
