"""westock CLI — 包装 lib/data_westock.py 的命令行接口

设计目标：
1. 提供统一 CLI 入口（scripts/），无需写 Python 即可拉数据
2. 统一 --end-date 语义：上游支持 → 透传；不支持 → post-filter
3. 多输出格式：markdown (默认) / csv / json
4. 与项目其它 CLI 风格一致：argparse + logging + 项目根加入 sys.path

上游 westock-data 不支持 --end-date 的子命令：kline / hot / quote
上游支持的：capital-flow (--date) / lhb (--date) / finance (无日期，按 num 期数)

用法：
    python scripts/westock_cli.py kline 600519 --end-date 2024-01-01
    python scripts/westock_cli.py kline 600519 --period week --limit 60
    python scripts/westock_cli.py capital-flow 600519 --end-date 2024-12-31
    python scripts/westock_cli.py lhb --end-date 2024-12-31
    python scripts/westock_cli.py hot --csv
    python scripts/westock_cli.py quote 600519 --json
    python scripts/westock_cli.py finance 600519 --type lrb --num 4
"""
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

# 项目根加入 sys.path（与项目其它 scripts 风格一致）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.data import data_westock as ws  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("westock_cli")

# 数据子包的日期列名约定（不同子命令上游返回的列名可能不同）
DATE_COLUMN_CANDIDATES = ("日期", "date", "时间", "trade_date", "data_date")


def _filter_end_date(df: pd.DataFrame, end_date: str) -> pd.DataFrame:
    """按 --end-date 过滤 DataFrame

    业务语义：保留 date_col <= end_date 的所有行（包含 end_date 当天）
    上游不传 end_date 时本函数不被调用；本函数只在 post-filter 场景下生效

    Args:
        df: westock 上游返回的 DataFrame
        end_date: YYYY-MM-DD 格式截止日期

    Returns:
        过滤后的 DataFrame（按 date_col <= end_date 保留）

    设计决策（实现时考虑）：
    1. 日期列名约定 — 找 DATE_COLUMN_CANDIDATES 中第一个存在的列
    2. 日期格式兼容 — 上游可能返回 str / datetime / int (YYYYMMDD)
    3. 边界条件 — df 为空 / 缺列 / end_date 解析失败 → 静默返回原 df 还是警告？
    """
    if df.empty:
        return df
    col = next((c for c in DATE_COLUMN_CANDIDATES if c in df.columns), None)
    if col is None or pd.isna(cutoff := pd.to_datetime(end_date, errors="coerce")):
        logger.warning(f"_filter_end_date: 缺列或日期解析失败, 跳过过滤 (end_date={end_date})")
        return df
    return df[pd.to_datetime(df[col], errors="coerce") <= cutoff]


def _print_df(df: pd.DataFrame, fmt: str) -> None:
    """按 fmt 输出 df: markdown (默认) / csv / json"""
    if df.empty:
        print("(no data)", file=sys.stderr)
        return
    if fmt == "json":
        print(df.to_json(orient="records", force_ascii=False))
    elif fmt == "csv":
        print(df.to_csv(index=False))
    else:  # markdown
        try:
            print(df.to_markdown(index=False))
        except Exception:
            # tabulate 不可用时降级
            print(df.to_string(index=False))


def _print_dict(d: Dict[str, Any], fmt: str) -> None:
    """quote 子命令用：上游返回 dict 而非 DataFrame"""
    if not d:
        print("(no data)", file=sys.stderr)
        return
    if fmt == "json":
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        for k, v in d.items():
            print(f"{k}: {v}")


# ─────────────────────────────────────────────────────────────
# Subcommand handlers
# ─────────────────────────────────────────────────────────────

def cmd_kline(args: argparse.Namespace) -> None:
    """K线 — 上游无 --end-date，走 post-filter"""
    df = ws.get_kline(args.code, period=args.period, limit=args.limit, fq=args.fq)
    if args.end_date and not df.empty:
        df = _filter_end_date(df, args.end_date)
    _print_df(df, args.fmt)


def cmd_finance(args: argparse.Namespace) -> None:
    """财务报表 — 无日期参数（按 --num 期数取最近 N 期）"""
    df = ws.get_finance(args.code, ftype=args.type, num=args.num)
    _print_df(df, args.fmt)


def cmd_capital_flow(args: argparse.Namespace) -> None:
    """资金流向 — 上游 --date 直接透传"""
    df = ws.get_capital_flow(args.code, date=args.end_date)
    _print_df(df, args.fmt)


def cmd_lhb(args: argparse.Namespace) -> None:
    """龙虎榜 — 上游 --date 直接透传"""
    df = ws.get_lhb(date=args.end_date)
    _print_df(df, args.fmt)


def cmd_hot(args: argparse.Namespace) -> None:
    """热门股票 — 上游无日期，post-filter"""
    df = ws.get_hot()
    if args.end_date and not df.empty:
        df = _filter_end_date(df, args.end_date)
    _print_df(df, args.fmt)


def cmd_quote(args: argparse.Namespace) -> None:
    """实时行情 — 单值 dict，无日期参数"""
    quote = ws.get_quote(args.code)
    _print_dict(quote, args.fmt)


# ─────────────────────────────────────────────────────────────
# Argparse setup
# ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="westock_cli",
        description="westock CLI wrapper — 包装 lib/data_westock.py",
    )

    # --csv/--json 共享给所有 subparser (parent parser 模式)
    fmt_parent = argparse.ArgumentParser(add_help=False)
    fmt_parent.add_argument("--csv", action="store_const", const="csv", dest="fmt",
                           help="输出 CSV")
    fmt_parent.add_argument("--json", action="store_const", const="json", dest="fmt",
                           help="输出 JSON")

    sub = parser.add_subparsers(dest="command", required=True, metavar="CMD")

    # kline
    p = sub.add_parser("kline", parents=[fmt_parent], help="K线数据 (上游无 --end-date, post-filter)")
    p.set_defaults(fmt="markdown")
    p.add_argument("code", help="股票代码 (如 600519)")
    p.add_argument("--period", default="day", choices=["day", "week", "month", "season", "year"])
    p.add_argument("--limit", type=int, default=200, help="数据条数 (最大 2000)")
    p.add_argument("--fq", default="qfq", choices=["qfq", "hfq", "bfq"])
    p.add_argument("--end-date", help="截止日期 YYYY-MM-DD (post-filter)")
    p.set_defaults(func=cmd_kline)

    # finance
    p = sub.add_parser("finance", parents=[fmt_parent], help="财务报表 (按 --num 期数取最近 N 期)")
    p.set_defaults(fmt="markdown")
    p.add_argument("code")
    p.add_argument("--type", default="lrb", choices=["lrb", "zcfz", "xjll"],
                   dest="type", help="lrb=利润表 / zcfz=资产负债表 / xjll=现金流量表")
    p.add_argument("--num", type=int, default=4)
    p.set_defaults(func=cmd_finance)

    # capital-flow
    p = sub.add_parser("capital-flow", parents=[fmt_parent], help="A股主力资金 (上游 --date 透传)")
    p.set_defaults(fmt="markdown")
    p.add_argument("code")
    p.add_argument("--end-date", help="日期 YYYY-MM-DD (透传给上游 --date)")
    p.set_defaults(func=cmd_capital_flow)

    # lhb
    p = sub.add_parser("lhb", parents=[fmt_parent], help="龙虎榜 (上游 --date 透传)")
    p.set_defaults(fmt="markdown")
    p.add_argument("--end-date", help="日期 YYYY-MM-DD")
    p.set_defaults(func=cmd_lhb)

    # hot
    p = sub.add_parser("hot", parents=[fmt_parent], help="热门股票 (post-filter)")
    p.set_defaults(fmt="markdown")
    p.add_argument("--end-date", help="截止日期 YYYY-MM-DD")
    p.set_defaults(func=cmd_hot)

    # quote
    p = sub.add_parser("quote", parents=[fmt_parent], help="实时行情 (单值, 无日期参数)")
    p.set_defaults(fmt="markdown")
    p.add_argument("code")
    p.set_defaults(func=cmd_quote)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except NotImplementedError as e:
        print(f"FIXME: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        logger.exception("子命令执行失败: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
