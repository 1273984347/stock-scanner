"""P0 回归判据：资金流「代码统一 6 位」+「缺失显式化」

背景（2026-11-20 实测取证，勿删）：

1. **代码口径漂移**：市场批量层（同花顺即时）的 code 列前导零在解析阶段即丢失
   （实测 `cache/fundflow__market.parquet` 的 key 为 '1' / '10' / '858' …），
   而查询侧用 6 位代码 ⇒ 深市 000/002、创业板 300、北交所 4/8/92 **批量层 100% MISSING**，
   直接落默认 0 ⇒ 资金面维以「假零值」参与四维加权。

2. **缺失伪装成中性**：实测 `score_capital_flow(main_net_ratio=0, net_inflow_5d=0.0)`
   = 1.0 分（命中 `elif main_net_ratio > -0.05`），与真实中性(2.0)/强流出(0.0) 不可区分。
   `strategies._compute_base_scores` 的 `ff = fund_flow if fund_flow is not None else ...`
   使全零 dict 绕过逐股重试 ⇒ 缺失永远静默。

3. **口径决策（用户裁定 2026-11-20）**：缺失维**从四维加权中剔除**，其余三维权重归一；
   不以 0 分冒充「中性」，也不让缺失股直接出局。

判据覆盖：
- 6 位归一（输入形态 × 4 类）
- 解析层 / 缓存读回层 / 查询层三处口径一致
- 0 开头代码能命中批量层（核心回归）
- 数据层缺失**显式抛错**（熔断中 / 全源失败），真实零值**不抛**
- 批量层逐股回退失败 ⇒ 显式缺失标记（不是全 0）
- 评分层：缺失维剔除 + 三维权重归一；正常路径数值**逐字节不变**

全程 hermetic fake，不触网、不落真盘。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd
import pytest

import lib.data.data_layer as data_layer
from lib.data.data_layer import FundFlowUnavailableError


# ---------------------------------------------------------------------------
# 工具：隔离进程内缓存 / 熔断器 / 批量层失败标记
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_fundflow_state():
    data_layer._fundflow_cache.clear()
    data_layer._FUND_FLOW_BREAKER.reset()
    data_layer._fundflow_market_unavailable_until = 0.0
    yield
    data_layer._fundflow_cache.clear()
    data_layer._FUND_FLOW_BREAKER.reset()
    data_layer._fundflow_market_unavailable_until = 0.0


def _install_source_fakes(monkeypatch, disk_store, value_fn=None, raises=None):
    """安装 hermetic fakes：逐股 fetch / 磁盘缓存 / 批量层。"""
    calls = {"n": 0}

    def fake_fetch(code):
        calls["n"] += 1
        if raises is not None:
            raise raises(code)
        if value_fn is not None:
            return value_fn(code)
        return {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}

    def fake_load(key, max_age_hours=2):
        return disk_store.get(key, pd.DataFrame())

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", fake_fetch)
    monkeypatch.setattr(data_layer, "_load_cache", fake_load)
    monkeypatch.setattr(data_layer, "_save_cache",
                        lambda key, df: disk_store.__setitem__(key, df))
    return calls


def _install_market_layer(monkeypatch, market_map):
    """直接替换批量层（绕过网络），返回调用计数。"""
    calls = {"n": 0}

    def fake_market():
        calls["n"] += 1
        return dict(market_map)

    monkeypatch.setattr(data_layer, "_get_market_fund_flow", fake_market)
    monkeypatch.setattr(data_layer, "_load_cache",
                        lambda key, max_age_hours=2: pd.DataFrame())
    monkeypatch.setattr(data_layer, "_save_cache", lambda key, df: None)
    return calls


# ===========================================================================
# 1) 代码归一：6 位纯数字
# ===========================================================================

def test_normalize_code_pads_leading_zeros():
    """前导零丢失是本次根因：'858' 必须归一回 '000858'。"""
    from lib.data.market_utils import normalize_code

    assert normalize_code("858") == "000858"
    assert normalize_code("1") == "000001"
    assert normalize_code(858) == "000858"
    assert normalize_code("000858") == "000858"
    assert normalize_code("600519") == "600519"


def test_normalize_code_strips_market_prefix():
    """带 sh/sz/bj 前缀的代码同样归一为 6 位纯数字。"""
    from lib.data.market_utils import normalize_code

    assert normalize_code("sz000858") == "000858"
    assert normalize_code("sh600519") == "600519"
    assert normalize_code("bj830799") == "830799"
    assert normalize_code("SZ000858") == "000858"


# ===========================================================================
# 2) 三处口径一致：解析层 / 缓存读回层 / 查询层
# ===========================================================================

def test_parse_market_fund_flow_returns_six_digit_keys():
    """解析层：code 列前导零丢失必须被归一回 6 位。"""
    df = pd.DataFrame({
        "股票代码": [858, 1, 600519],
        "净额": [1.0e8, 2.0e8, 3.0e8],
        "成交额": [1.0e9, 2.0e9, 3.0e9],
    })

    out = data_layer._parse_market_fund_flow(df)

    assert set(out) == {"000858", "000001", "600519"}, f"实际 key: {sorted(out)}"
    assert out["000858"]["net_inflow_5d"] == 1.0e8


def test_market_df_to_map_normalizes_legacy_dirty_cache():
    """缓存读回层：存量脏缓存（key='858'）读回时归一，避免存量数据全部失效。"""
    dirty = pd.DataFrame([{
        "code": "858", "main_net_ratio": 0.05, "net_inflow_5d": 4.0e7,
    }])

    out = data_layer._market_df_to_map(dirty)

    assert "000858" in out, f"实际 key: {sorted(out)}"
    assert out["000858"]["main_net_ratio"] == 0.05


def test_batch_fund_flow_hits_leading_zero_code(monkeypatch):
    """核心回归：0 开头代码必须命中批量层（修前恒返默认 0）。"""
    market = {"000858": {"main_net_ratio": 0.04, "net_inflow_5d": 3.0e7}}
    _install_market_layer(monkeypatch, market)

    out = data_layer._batch_fund_flow(["000858"])

    assert out["000858"]["net_inflow_5d"] == 3.0e7, (
        f"0 开头代码未命中批量层，实际 {out.get('000858')}"
    )
    assert out["000858"]["main_net_ratio"] == 0.04


# ===========================================================================
# 3) 数据层：缺失显式抛错，真实零值不抛
# ===========================================================================

def test_get_stock_fund_flow_raises_when_all_sources_fail(monkeypatch):
    """全源失败 ⇒ 显式抛错（修前静默返回全零 dict）。"""
    disk_store = {}
    _install_source_fakes(
        monkeypatch, disk_store,
        raises=lambda code: FundFlowUnavailableError(f"all sources down: {code}"),
    )

    with pytest.raises(FundFlowUnavailableError):
        data_layer.get_stock_fund_flow("600519")


def test_get_stock_fund_flow_raises_when_breaker_open(monkeypatch):
    """熔断开启 ⇒ 显式抛错（修前静默返回全零 dict）。"""
    disk_store = {}
    calls = _install_source_fakes(
        monkeypatch, disk_store,
        raises=lambda code: FundFlowUnavailableError("down"),
    )

    for i in range(3):
        with pytest.raises(FundFlowUnavailableError):
            data_layer.get_stock_fund_flow(f"60000{i}")
    assert data_layer._FUND_FLOW_BREAKER.state == "open"

    # 熔断期间仍然抛错，且不再 fetch（fail-fast 性能语义不变）
    with pytest.raises(FundFlowUnavailableError):
        data_layer.get_stock_fund_flow("600099")
    assert calls["n"] == 3, f"熔断后应停止 fetch，实际 {calls['n']} 次"


def test_get_stock_fund_flow_keeps_genuine_zero(monkeypatch):
    """真实零值（停牌/零流）≠ 缺失：仍返回零 dict，不抛错、不触发熔断。"""
    disk_store = {}

    def genuine_zero(code):
        return {"main_net_ratio": 0, "net_inflow_5d": 0}

    _install_source_fakes(monkeypatch, disk_store, value_fn=genuine_zero)

    for _ in range(5):
        res = data_layer.get_stock_fund_flow("000003")
        assert res == {"main_net_ratio": 0, "net_inflow_5d": 0}

    assert data_layer._FUND_FLOW_BREAKER.state == "closed"


# ===========================================================================
# 4) 批量层逐股回退失败 ⇒ 显式缺失标记（不是全 0）
# ===========================================================================

def test_batch_fund_flow_marks_missing_explicitly(monkeypatch):
    """批量未命中 + 逐股失败 ⇒ 显式缺失标记，不得伪装成全零。"""
    disk_store = {}
    _install_market_layer(monkeypatch, {})
    _install_source_fakes(
        monkeypatch, disk_store,
        raises=lambda code: FundFlowUnavailableError(f"down: {code}"),
    )

    out = data_layer._batch_fund_flow(["000858"])

    entry = out["000858"]
    assert entry is not None, "缺失必须以显式标记出现，不得缺席"
    assert entry.get("main_net_ratio") is None, (
        f"缺失不得用全零冒充，实际 {entry}"
    )


# ===========================================================================
# 5) 评分层：缺失维剔除 + 三维权重归一
# ===========================================================================

def test_comprehensive_score_unchanged_when_all_dimensions_present():
    """正常路径数值必须逐字节不变（L5 基线不漂移的前提）。"""
    from lib.scoring.scorer import comprehensive_score

    out = comprehensive_score(8.0, 6.0, 5.0, 5.0)

    # 8*0.35 + 6*0.35 + 5*0.20 + 5*0.10 = 6.4
    assert out["score"] == 6.4
    assert out["rating"] == "买入"
    assert out["breakdown"]["capital_flow"] == 6.0


def test_comprehensive_score_drops_missing_dimension_and_renormalizes():
    """资本流缺失 ⇒ 剔除该维，其余三维按原权重比例归一。"""
    from lib.scoring.scorer import comprehensive_score

    out = comprehensive_score(8.0, None, 5.0, 5.0)

    # (8*0.35 + 5*0.20 + 5*0.10) / (0.35+0.20+0.10) = 4.3 / 0.65 = 6.6153...
    assert out["score"] == 6.62
    assert out["breakdown"]["capital_flow"] is None, (
        "缺失维必须显式标记为 None，不得与 0 分混淆"
    )


def test_compute_base_scores_passes_none_when_fund_flow_missing(monkeypatch):
    """策略层：缺失的资金流不得再以 1.0 分（假中性）参与打分。"""
    from lib.strategy import strategies as st

    row = pd.Series({"代码": "000858", "涨跌幅": 1.0, "成交额": 1.0e8})
    monkeypatch.setattr(st, "get_news_signals",
                        lambda code: {"major_positive": False, "institutional_buy": False,
                                      "no_negative": True, "earnings_positive": False})

    missing = getattr(data_layer, "FUND_FLOW_MISSING",
                      {"main_net_ratio": None, "net_inflow_5d": None})

    out = st._compute_base_scores(row, "000858", 0.01, 0.02, missing, None)

    assert out["breakdown"]["capital_flow"] is None, (
        f"缺失维不得拿假中性分，实际 {out['breakdown'].get('capital_flow')}"
    )


# ===========================================================================
# 6) 上游 NaN（=数据缺失）不得被 fillna(0) 伪装成「真实零值」
# ===========================================================================
# 实测（2026-11-20）：同花顺即时资金流接口 ak.stock_fund_flow_individual
# 返回 5216 行，但「净额」「成交额」等资金流列**几乎全为 NaN**（仅 4 条非零）。
# _parse_market_fund_flow 的 fillna(0) 把 NaN 变成 0 ⇒ 5209 条「假零值」
# 冒充有效数据 ⇒ 评分层拿到 main_net_ratio=0 → score_capital_flow 得 1.0 分
# （假中性）。缺失显式化必须覆盖这一层，否则 P0 只修了一半。

def test_parse_market_fund_flow_drops_all_nan_rows():
    """净额列全 NaN 的股票 = 上游没给数据 ⇒ 不得进结果（更不得填 0）。"""
    df = pd.DataFrame({
        "股票代码": [301132, 300812, 600519],
        "净额": [None, None, 3.0e8],
        "成交额": [None, None, 3.0e9],
    })

    out = data_layer._parse_market_fund_flow(df)

    assert "301132" not in out, f"全 NaN 的股票不得进有效结果，实际 {sorted(out)}"
    assert "300812" not in out, f"全 NaN 的股票不得进有效结果，实际 {sorted(out)}"
    assert out["600519"]["net_inflow_5d"] == 3.0e8, "有数据的股票必须保留"


def test_parse_market_fund_flow_keeps_partial_rows():
    """同组内部分行缺净额 ⇒ 按有数据的行求和（NaN 跳过，不污染整组）。"""
    df = pd.DataFrame({
        "股票代码": [600519, 600519, 600519],
        "净额": [1.0e8, None, 2.0e8],
        "成交额": [1.0e9, None, 2.0e9],
    })

    out = data_layer._parse_market_fund_flow(df)

    assert out["600519"]["net_inflow_5d"] == 3.0e8


def test_market_df_to_map_skips_zero_only_entries():
    """存量脏缓存（全零表）读回时必须跳过，否则「假零值」继续冒充有效数据。"""
    dirty = pd.DataFrame([
        {"code": "858", "main_net_ratio": 0.0, "net_inflow_5d": 0.0},
        {"code": "600519", "main_net_ratio": 0.05, "net_inflow_5d": 4.0e7},
    ])

    out = data_layer._market_df_to_map(dirty)

    assert "000858" not in out, f"全零条目不得进有效结果，实际 {sorted(out)}"
    assert "600519" in out, "有真实值的条目必须保留"


def test_get_market_fund_flow_refetches_when_cached_table_all_zero(monkeypatch):
    """缓存里是全零脏表 ⇒ 视为不可用 ⇒ 重新拉取（不得把脏表当真源用）。"""
    disk_store = {
        "fundflow/_market": pd.DataFrame([
            {"code": "858", "main_net_ratio": 0.0, "net_inflow_5d": 0.0},
        ]),
    }
    calls = {"n": 0}

    def fake_fetch_market():
        calls["n"] += 1
        return {"600519": {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}}

    monkeypatch.setattr(data_layer, "_fetch_market_fund_flow", fake_fetch_market)
    monkeypatch.setattr(data_layer, "_load_cache",
                        lambda key, max_age_hours=2: disk_store.get(key, pd.DataFrame()))
    monkeypatch.setattr(data_layer, "_save_cache",
                        lambda key, df: disk_store.__setitem__(key, df))

    out = data_layer._get_market_fund_flow()

    assert calls["n"] == 1, "脏缓存必须触发一次重新拉取"
    assert out.get("600519", {}).get("net_inflow_5d") == 1.2e8


def test_parse_market_fund_flow_handles_cn_unit_strings():
    """P-121（2026-11-21）：THS 净额/成交额是中文单位数字串（"1.11亿"/"-9225.10万"），
    pd.to_numeric 会整体 coerce 成 NaN——11-20 的「净额几乎全 NaN」由此伪装成
    上游失效。解析层必须先还原中文单位，再按 P-114 缺失语义处理（"--" 仍不进结果）。
    """
    df = pd.DataFrame({
        "股票代码": ["600519", "000001", "300001"],
        "净额": ["1.11亿", "-9225.10万", "--"],
        "成交额": ["8.52亿", "5.03亿", ""],
    })
    m = data_layer._parse_market_fund_flow(df)
    assert set(m) == {"600519", "000001"}, "'--'/空串 = 真实缺失，不产出假零值"
    assert m["600519"]["net_inflow_5d"] == pytest.approx(1.11e8)
    assert m["600519"]["main_net_ratio"] == pytest.approx(1.11e8 / 8.52e8)
    assert m["000001"]["net_inflow_5d"] == pytest.approx(-9225.10 * 1e4)
    assert m["000001"]["main_net_ratio"] == pytest.approx(
        -9225.10e4 / 5.03e8)


def test_cn_number_to_float_accepts_plain_numbers():
    """纯数字/千分位输入兼容（上游格式切换不炸）。"""
    f = data_layer._cn_number_to_float
    assert f(123.0) == 123.0
    assert f("1,234.5") == 1234.5
    assert f("2.3亿") == pytest.approx(2.3e8)
    assert f("-546.54万") == -546.54e4
    import math
    assert math.isnan(f("--"))
    assert math.isnan(f(None))
