"""WeStock 六类数据命令契约判据（2026-11-22 核验批次）

背景：端到端实测发现 data_westock.py 三个函数命令用错——
- get_quote 走 npm CLI ``quote``（当前渠道不可用，实测返回空）→ 改走 Go CLI
- get_hot  走 npm CLI ``hot``（缺子命令，实测返回空）→ 改走 Go CLI ``hot stock``
- get_lhb  走 npm CLI ``lhb`` 但没传股票码（实测报错）→ code 设为必填

判据钉死两类不变量（P-112 推广：第三方 CLI 无版本契约，必须把命令形态钉进测试）：
1. 每个函数下发的 argv 首段 == 期望命令链（升级改命令即判红）
2. 坏命令不回归（quote 不得再走 npm / hot 不得裸调用 / lhb 必带 code）
3. Go CLI 未安装或返回空时降级为空 DataFrame/dict，不抛错（保生产链路）
"""
import logging
import os
import sys

sys.path.insert(0, ".")

import pandas as pd
import pytest

from lib.data import data_westock as ws


@pytest.fixture
def capture_npm(monkeypatch):
    calls = []
    monkeypatch.setattr(ws, "_run_westock", lambda args, timeout=30: (calls.append(args), "")[1])
    monkeypatch.setattr(ws, "_run_go", lambda args, timeout=30: (calls.append(args), "")[1])
    return calls


@pytest.fixture
def capture_go(monkeypatch):
    calls = []
    monkeypatch.setattr(ws, "_run_go", lambda args, timeout=30: (calls.append(args), "")[1])
    return calls


# ── 坏命令不回归 ────────────────────────────────────────────

def test_quote_uses_go_cli_not_npm(capture_go, monkeypatch):
    """quote 必须走 Go CLI；npm CLI 该命令已失效（2026-11-22 实测）。"""
    monkeypatch.setattr(
        ws, "_run_westock",
        lambda args, timeout=30: pytest.fail("quote 不得再调用 npm CLI"),
    )
    assert ws.get_quote("600519") == {}
    assert capture_go == [["quote", "sh600519"]]


def test_hot_uses_subcommand(capture_go):
    """hot 必须带 kind 子命令；裸 hot 实测返回空。"""
    ws.get_hot()
    assert capture_go == [["hot", "stock", "--limit", "50"]]


def test_hot_rejects_invalid_kind(capture_go):
    with pytest.raises(ValueError, match="invalid hot kind"):
        ws.get_hot("bogus")
    assert capture_go == []


def test_lhb_requires_code(capture_npm):
    """lhb 不带股票码 = 上游报错，code 必须必填。"""
    with pytest.raises(TypeError):
        ws.get_lhb()
    ws.get_lhb("000001", date="2026-11-20")
    assert capture_npm[-1] == ["lhb", "sz000001", "--date", "2026-11-20"]


# ── 六类命令链契约 ──────────────────────────────────────────

@pytest.mark.parametrize("fn,expected", [
    (lambda: ws.get_kline("600519", limit=5), ["kline", "sh600519", "--period", "day", "--limit", "5", "--fq", "qfq"]),
    (lambda: ws.get_finance("600519", num=2), ["finance", "sh600519", "--type", "lrb", "--num", "2"]),
    (lambda: ws.get_capital_flow("600519"), ["asfund", "sh600519"]),
    (lambda: ws.get_technical("600519"), ["technical", "sh600519"]),
])
def test_npm_commands_contract(capture_npm, fn, expected):
    """npm CLI 生产命令：kline/finance/asfund/technical 链钉死。"""
    fn()
    assert capture_npm == [expected]


@pytest.mark.parametrize("fn,expected", [
    (lambda: ws.get_news("600519"), ["news", "list", "sh600519", "--limit", "20"]),
    (lambda: ws.get_report("600519"), ["report", "list", "sh600519", "--limit", "20"]),
    (lambda: ws.get_notice("600519"), ["notice", "list", "sh600519", "--limit", "20"]),
    (lambda: ws.get_sector_rank(), ["sector", "ranking", "--kind", "industry", "--limit", "50"]),
    (lambda: ws.get_index_constituent("000300"), ["index", "constituent", "sh000300"]),
    (lambda: ws.get_macro("cn_core"), ["macro", "indicator", "cn_core", "--limit", "3"]),
    (lambda: ws.get_fund_north("600519"), ["fund", "north-holding", "sh600519"]),
    (lambda: ws.get_fund_south("600519"), ["fund", "south-holding", "sh600519"]),
    (lambda: ws.get_etf("510050"), ["etf", "profile", "sh510050"]),
])
def test_go_capability_commands_contract(capture_go, fn, expected):
    """Go CLI 补充能力：新闻/研报/公告/板块/指数/宏观/北向/南下/ETF 链钉死。"""
    fn()
    assert capture_go == [expected]


# ── 降级语义 ────────────────────────────────────────────────

def test_go_cli_missing_degrades_to_empty(monkeypatch):
    """Go CLI 未安装时 GO_CMD=[]，所有能力降级为空，不抛错。"""
    monkeypatch.setattr(ws, "GO_CMD", [])
    assert ws._run_go(["quote", "sh600519"]) == ""
    assert ws.get_quote("600519") == {}
    assert ws.get_hot().empty
    assert ws.get_news("600519").empty


class _LogHandler(logging.Handler):
    """直挂 ws.logger 收集告警（conftest 把 lib logger 设为 propagate=0）。"""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_unauthenticated_go_cli_degrades_all_capabilities_and_redacts_auth_url(tmp_path, monkeypatch):
    """真实未鉴权返回形态（rc=1 + AUTH_REQUIRED URL）：全部能力降级，且授权 URL 不进日志。"""
    script = tmp_path / "fake_go_auth.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('AUTH_REQUIRED:https://gu.qq.com/resource/westockCliAuth/index.html"
        "?code=SECRET123&channel=wbgeneral\\n')\n"
        "sys.stderr.write('NEXT:westock auth fetch --wait 300\\n')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ws, "GO_CMD", [sys.executable, str(script)])

    handler = _LogHandler()
    ws.logger.addHandler(handler)
    try:
        assert ws._run_go(["quote", "sh600519"]) == ""
        assert ws.get_quote("600519") == {}
        for result in (
            ws.get_hot(),
            ws.get_news("600519"),
            ws.get_report("600519"),
            ws.get_notice("600519"),
            ws.get_sector_rank(),
            ws.get_index_constituent("000001"),
            ws.get_macro("CPI"),
            ws.get_fund_north("600519"),
            ws.get_fund_south("600519"),
            ws.get_etf("510300"),
        ):
            assert result.empty
    finally:
        ws.logger.removeHandler(handler)

    joined = "\n".join(handler.messages)
    assert handler.messages
    assert "AUTH_REQUIRED:<redacted>" in joined
    assert "SECRET123" not in joined and "https://" not in joined


def test_index_code_differs_from_stock_code():
    """指数 000300=sh000300（个股规则会错成 sz000300）——独立映射判据。"""
    assert ws._to_index_code("000300") == "sh000300"
    assert ws._to_index_code("399001") == "sz399001"
    assert ws._to_westock_code("600519") == "sh600519"
    assert ws._to_westock_code("000300") == "sz000300"  # 个股规则≠指数规则


def test_markdown_table_parse():
    """Markdown 表格解析：英文表头 + 中文表头双形态（P-112）。"""
    en = "| date | open | last |\n| --- | --- | --- |\n| 2026-11-20 | 1 | 2 |\n"
    df = ws._parse_markdown_table(en)
    assert list(df.columns) == ["date", "open", "last"]
    assert len(df) == 1
    assert ws._parse_markdown_table("").empty


def test_markdown_table_keeps_empty_cells():
    """P-127：合法空单元格（quote price_currency / hot status）不得整行丢弃。"""
    out = ("| code | currency | price |\n| --- | --- | --- |\n"
           "| sh600519 |  | 1485.75 |\n| sh600036 |  | 45.20 |\n")
    df = ws._parse_markdown_table(out)
    assert df.shape == (2, 3)
    assert df["currency"].tolist() == ["", ""]
    assert df["price"].tolist() == ["1485.75", "45.20"]


def test_markdown_table_splits_multiple_tables():
    """P-128：多张表由非 '|' 行分隔时，只解析第一张，附表不混入也不刷 warning。"""
    out = (
        "| date | price |\n| --- | --- |\n| 2026-11-22 | 10.5 |\n"
        "\n**资产配置**\n\n| name | ratio |\n| --- | --- |\n| 股票 | 95.45 |\n"
    )
    df = ws._parse_markdown_table(out)
    assert df.shape == (1, 2)
    assert list(df.columns) == ["date", "price"]
    assert df["price"].tolist() == ["10.5"]
    # 可按需取附表
    df2 = ws._parse_markdown_table(out, table_index=1)
    assert df2.shape == (1, 2)
    assert df2["name"].tolist() == ["股票"]
    # 单表输出行为不变
    assert ws._parse_markdown_table("| a |\n| --- |\n| 1 |\n").shape == (1, 1)


def test_etf_requires_subcommand(capture_go):
    """Go CLI etf 裸调用返回 Usage 提示，必须带 profile/overview/holdings/nav。"""
    ws.get_etf("510050")
    assert capture_go == [["etf", "profile", "sh510050"]]
    with pytest.raises(ValueError, match="invalid etf aspect"):
        ws.get_etf("510050", aspect="")
