"""D4 (2026-06-09): westock_cli 统一 --end-date flag 测试

覆盖：
- _filter_end_date 5 个用例：正常 / 空 df / 缺列 / 坏日期 / 多日期列名
- CLI argparse 5 个用例：parser 构建 / flag 解析 / 子命令分发 / end-date 透传 / 端到端 mock

上游 westock-data npx CLI 不支持 kline/hot 的 --end-date,
所以 _filter_end_date 是 post-filter 的关键 — 测试覆盖必须全。
"""
import os, sys
sys.path.insert(0, '.')
sys.path.insert(0, 'scripts')
os.environ['NO_VERIFY_TDX'] = '1'

import logging
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

import westock_cli as wc


# ─────────────────────────────────────────────────────────────
# _filter_end_date 单元测试 (5 用例)
# ─────────────────────────────────────────────────────────────

def test_filter_end_date_normal_case():
    """正常: 保留 date <= end_date 的所有行 (含 end_date 当天)"""
    df = pd.DataFrame({
        "日期": ["2024-01-15", "2024-02-15", "2024-03-15"],
        "close": [10, 11, 12],
    })
    result = wc._filter_end_date(df, "2024-02-15")
    assert len(result) == 2, f"应保留 2 行, 实际 {len(result)}"
    assert result.iloc[-1]["close"] == 11, "end_date 当天应被保留"


def test_filter_end_date_empty_df():
    """空 df: 直接返回, 不应 raise"""
    df = pd.DataFrame()
    result = wc._filter_end_date(df, "2024-02-15")
    assert result.empty, "空 df 应返回空 df"


def test_filter_end_date_missing_date_column(caplog):
    """缺日期列: warn + 返回原 df (不破坏数据流)"""
    df = pd.DataFrame({"foo": [1, 2, 3]})
    with caplog.at_level(logging.WARNING, logger="westock_cli"):
        result = wc._filter_end_date(df, "2024-02-15")
    assert len(result) == 3, "缺列应返回原 df"
    assert any("缺列" in r.message for r in caplog.records), \
        "应打 warning 提示缺列"


def test_filter_end_date_invalid_date_string(caplog):
    """end_date 解析失败: warn + 返回原 df"""
    df = pd.DataFrame({"日期": ["2024-01-15", "2024-02-15"], "close": [10, 11]})
    with caplog.at_level(logging.WARNING, logger="westock_cli"):
        result = wc._filter_end_date(df, "not-a-date")
    assert len(result) == 2, "坏日期应返回原 df"
    assert any("缺列或日期解析失败" in r.message for r in caplog.records), \
        "应打 warning 提示日期解析失败"


def test_filter_end_date_alternate_column_name():
    """多日期列名兼容: 'date' / '时间' 列也能被识别"""
    df = pd.DataFrame({
        "date": ["2024-01-15", "2024-02-15", "2024-03-15"],
        "close": [10, 11, 12],
    })
    result = wc._filter_end_date(df, "2024-02-15")
    assert len(result) == 2, f"应识别 'date' 列, 实际保留 {len(result)} 行"


# ─────────────────────────────────────────────────────────────
# CLI argparse 测试 (5 用例)
# ─────────────────────────────────────────────────────────────

def test_parser_builds_all_subcommands():
    """parser 应注册全部 6 个子命令"""
    import argparse as _ap
    parser = wc.build_parser()
    # 找 _SubParsersAction (标准 introspection 方式)
    sub_action = next(
        (a for a in parser._actions if isinstance(a, _ap._SubParsersAction)),
        None
    )
    assert sub_action is not None, "应有 subparsers"
    expected = {"kline", "finance", "capital-flow", "lhb", "hot", "quote"}
    actual = set(sub_action.choices.keys())
    assert expected.issubset(actual), \
        f"子命令缺失: 期望 {expected}, 实际 {actual}"


def test_kline_accepts_end_date():
    """kline 子命令应接受 --end-date flag"""
    parser = wc.build_parser()
    args = parser.parse_args(["kline", "600519", "--end-date", "2024-01-01"])
    assert args.command == "kline"
    assert args.code == "600519"
    assert args.end_date == "2024-01-01"
    assert args.period == "day"  # default
    assert args.fq == "qfq"  # default


def test_capital_flow_end_date_passes_through_to_upstream():
    """capital-flow 应把 --end-date 透传给 ws.get_capital_flow(date=...)"""
    with patch.object(wc.ws, "get_capital_flow", return_value=pd.DataFrame()) as mock_cf:
        args = wc.build_parser().parse_args(
            ["capital-flow", "600519", "--end-date", "2024-12-31"]
        )
        args.func(args)
        mock_cf.assert_called_once_with("600519", date="2024-12-31")


def test_kline_end_date_triggers_post_filter():
    """kline 上游不支持 date, 应走 _filter_end_date (post-filter)"""
    fake_df = pd.DataFrame({
        "日期": ["2024-01-15", "2024-02-15", "2024-03-15"],
        "close": [10, 11, 12],
    })
    with patch.object(wc.ws, "get_kline", return_value=fake_df), \
         patch.object(wc, "_print_df") as mock_print:
        args = wc.build_parser().parse_args(
            ["kline", "600519", "--end-date", "2024-02-15"]
        )
        args.func(args)
        # 验证 _print_df 收到的是过滤后的 df (2 行)
        printed_df = mock_print.call_args[0][0]
        assert len(printed_df) == 2, \
            f"post-filter 应保留 2 行, 实际 {len(printed_df)}"


def test_quote_returns_dict_no_date_filter():
    """quote 子命令无 --end-date, 应直接返回 dict"""
    with patch.object(wc.ws, "get_quote", return_value={"code": "600519", "price": 100}):
        args = wc.build_parser().parse_args(["quote", "600519"])
        args.func(args)  # 不应 raise


# ─────────────────────────────────────────────────────────────
# 输出格式 + 错误处理 (2 用例)
# ─────────────────────────────────────────────────────────────

def test_csv_format_outputs_csv(capsys):
    """--csv 应输出 CSV 格式"""
    fake_df = pd.DataFrame({"日期": ["2024-01-15"], "close": [10]})
    with patch.object(wc.ws, "get_kline", return_value=fake_df):
        args = wc.build_parser().parse_args(
            ["kline", "600519", "--csv", "--end-date", "2024-02-15"]
        )
        args.func(args)
        captured = capsys.readouterr()
        assert "日期" in captured.out and "close" in captured.out
        assert "," in captured.out, "CSV 应有逗号分隔"


def test_main_returns_error_for_unimplemented(capsys):
    """main() 对未实现的子命令应返回 exit code 2 (不 crash)"""
    # 模拟 _filter_end_date raise NotImplementedError
    with patch.object(wc, "_filter_end_date", side_effect=NotImplementedError("test")):
        fake_df = pd.DataFrame({"日期": ["2024-01-15"]})
        with patch.object(wc.ws, "get_kline", return_value=fake_df):
            rc = wc.main(["kline", "600519", "--end-date", "2024-02-15"])
            assert rc == 2, f"NotImplementedError 应返回 exit 2, 实际 {rc}"
            captured = capsys.readouterr()
            assert "FIXME" in captured.err or "未实现" in captured.err
