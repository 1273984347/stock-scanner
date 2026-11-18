"""P0 机器判据（ADR-0024，2026-11-18）：实时行情唯一实现 + 单位归一 + 降级显式化。

背景（坑表 P-99）：全市场实时行情曾有 4 套同名 ``get_realtime_quotes``，
其中 data_ashare 版只跑前 100 只、data_adata 版无人调用；腾讯解析另有两份
（data_tencent / data_ashare），且都直接透传「手 / 万元」，与 hist 的「股」相差
100 倍——资金流腾讯估算分支静默失效。本文件把这几条钉成判据。
"""
import ast
import pathlib

import pandas as pd
import pytest

import lib.data.data_realtime as rt

LIB_DATA = pathlib.Path(__file__).resolve().parent.parent / "lib" / "data"

# 2026-11-18 16:14:56 实测报文（贵州茅台，字段顺序未改动）
TENCENT_LINE_600519 = (
    'v_sh600519="1~贵州茅台~600519~1442.66~1457.70~1450.00~29847~15689~14158~'
    '1442.66~7~1442.65~5~1442.63~79~1442.50~1~1442.44~5~1443.00~1~1443.39~10~'
    '1443.49~2~1443.54~10~1443.60~2~~20261118161456~-15.04~-1.03~1456.00~'
    '1431.70~1442.66/29847/4316023208~29847~431602~0.24~21.95~~1456.00~'
    '1431.70~1.67~18034.43~18034.43~6.65~1603.47~1311.93~0.79~72~1446.05~'
    '20.98~21.91~~~-0.05~431602.3208~187.5458~13~   A~GP-A~"'
)


def _frame(rows: int, bj: int = 0) -> pd.DataFrame:
    """构造快照帧：bj 段用 92xxxx（北交所真实号段），其余用 60xxxx，互不撞号。"""
    codes = [f"{920000 + i:06d}" for i in range(bj)] + \
            [f"{600000 + i:06d}" for i in range(rows - bj)]
    return pd.DataFrame({
        "代码": codes,
        "名称": [f"股票{i}" for i in range(len(codes))],
        "最新价": 10.0,
        "涨跌幅": 1.0,
        "成交量": 1e6,
        "成交额": 1e7,
        "时间戳": "15:00:00",
    })


# ========== 单位契约 ==========

def test_tencent_volume_lot_converted_to_shares():
    """腾讯 parts[6] 是手 → 必须 ×100 归一到股（否则与 hist 差 100 倍）。"""
    df = rt.parse_tencent_quotes(TENCENT_LINE_600519)
    assert len(df) == 1
    assert df.iloc[0]["成交量"] == pytest.approx(29847 * 100), "手→股 未归一"
    assert df.iloc[0]["成交额"] == pytest.approx(431602.3208 * 10000), "万元→元 未归一"


def test_tencent_payload_field_mapping():
    """固定报文钉字段下标：价格/涨跌/高低/时间戳/代码不得错位。"""
    row = rt.parse_tencent_quotes(TENCENT_LINE_600519).iloc[0]
    assert row["代码"] == "600519"
    assert row["名称"] == "贵州茅台"
    assert row["最新价"] == pytest.approx(1442.66)
    assert row["昨收"] == pytest.approx(1457.70)
    assert row["今开"] == pytest.approx(1450.00)
    assert row["最高"] == pytest.approx(1456.00)
    assert row["最低"] == pytest.approx(1431.70)
    assert row["涨跌幅"] == pytest.approx(-1.03)
    assert row["时间戳"] == "16:14:56"
    assert row["市盈率-动态"] == pytest.approx(21.95)


def test_parse_rejects_truncated_payload():
    """字段不足 40 的行丢弃，不得产出半条脏数据。"""
    df = rt.parse_tencent_quotes('v_sh600519="1~贵州茅台~600519~1442.66"')
    assert df.empty


# ========== 降级校验 ==========

def test_validate_flags_small_frame_as_degraded():
    rep = rt.validate_snapshot(_frame(100, bj=10))
    assert rep["degraded"] is True
    assert any("4000" in w for w in rep["warnings"]), rep["warnings"]


def test_validate_flags_missing_bj():
    rep = rt.validate_snapshot(_frame(5000, bj=0))
    assert rep["bj_count"] == 0
    assert any("北交所" in w for w in rep["warnings"]), rep["warnings"]


def test_validate_full_market_is_clean():
    rep = rt.validate_snapshot(_frame(5000, bj=300))
    assert rep["degraded"] is False, rep["warnings"]
    assert rep["warnings"] == []
    assert rep["bj_count"] == 300 and rep["timestamp"] == "15:00:00"


def test_validate_flags_missing_contract_columns():
    df = _frame(5000, bj=10).drop(columns=["成交量"])
    rep = rt.validate_snapshot(df)
    assert rep["degraded"] is True
    assert any("成交量" in w for w in rep["warnings"]), rep["warnings"]


# ========== 源顺序与降级路径 ==========

def _no_cache(monkeypatch):
    monkeypatch.setattr(rt, "_load", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(rt, "_save", lambda *a, **k: None)


def test_primary_failure_falls_back_to_tencent_batch(monkeypatch):
    _no_cache(monkeypatch)
    monkeypatch.setattr(rt, "_load_universe",
                        lambda: pd.DataFrame({"代码": ["600519", "000001"]}))
    called = {}

    def fake_tencent(codes, **kw):
        called["codes"] = list(codes)
        return _frame(5000, bj=200)

    monkeypatch.setattr(rt, "fetch_tencent_quotes", fake_tencent)

    def boom():
        raise RuntimeError("sina down")

    df = rt.get_market_snapshot(force=True, primary_fetch=boom)
    assert not df.empty and len(df) == 5000
    assert called["codes"] == ["600519", "000001"], "腾讯兜底应只按缓存代码表重建"


def test_no_universe_skips_tencent_and_uses_pytdx(monkeypatch):
    """无代码表时不得发腾讯请求（并发 28 个空片无意义），直接降级 pytdx。"""
    _no_cache(monkeypatch)
    monkeypatch.setattr(rt, "_load_universe", lambda: pd.DataFrame())
    monkeypatch.setattr(rt, "fetch_tencent_quotes",
                        lambda *a, **k: pytest.fail("无代码表时不应调用腾讯批量"))
    monkeypatch.setattr(rt, "_fetch_pytdx_sample", lambda: _frame(28, bj=0))

    df = rt.get_market_snapshot(force=True, primary_fetch=lambda: pd.DataFrame())
    assert len(df) == 28


def test_all_sources_fail_returns_empty(monkeypatch, caplog):
    _no_cache(monkeypatch)
    monkeypatch.setattr(rt, "_load_universe", lambda: pd.DataFrame())
    monkeypatch.setattr(rt, "_fetch_pytdx_sample", lambda: pd.DataFrame())
    assert rt.get_market_snapshot(force=True, primary_fetch=lambda: pd.DataFrame()).empty


# ========== 缓存纪律 ==========

def test_degraded_snapshot_is_not_cached(monkeypatch):
    """100 行/28 行降级帧不得落缓存——否则整轮扫描的涨跌比分母是错的。"""
    monkeypatch.setattr(rt, "_load", lambda *a, **k: pd.DataFrame())
    saved = []
    monkeypatch.setattr(rt, "_save", lambda key, df: saved.append((key, len(df))))
    monkeypatch.setattr(rt, "_load_universe", lambda: pd.DataFrame())

    df = rt.get_market_snapshot(force=True, primary_fetch=lambda: _frame(100, bj=5))
    assert len(df) == 100
    assert saved == [], f"降级快照被写缓存: {saved}"


def test_full_snapshot_caches_and_persists_universe(monkeypatch):
    monkeypatch.setattr(rt, "_load", lambda *a, **k: pd.DataFrame())
    saved = {}
    monkeypatch.setattr(rt, "_save", lambda key, df: saved.__setitem__(key, len(df)))

    rt.get_market_snapshot(force=True, primary_fetch=lambda: _frame(5000, bj=300))
    assert saved.get(rt.SNAPSHOT_CACHE_KEY) == 5000
    assert saved.get(rt.UNIVERSE_CACHE_KEY) == 5000, "代码表未落盘，腾讯兜底将永远不可用"


def test_force_bypasses_cache(monkeypatch):
    monkeypatch.setattr(rt, "_load", lambda *a, **k: _frame(5000, bj=100))
    monkeypatch.setattr(rt, "_save", lambda *a, **k: None)
    hits = []
    rt.get_market_snapshot(force=True, primary_fetch=lambda: hits.append(1) or _frame(5000, bj=100))
    assert hits == [1], "force=True 仍读了缓存"


def test_non_force_reads_cache(monkeypatch):
    monkeypatch.setattr(rt, "_load", lambda *a, **k: _frame(5000, bj=100))
    monkeypatch.setattr(rt, "_save", lambda *a, **k: None)
    df = rt.get_market_snapshot(force=False, primary_fetch=lambda: pytest.fail("不应打主源"))
    assert len(df) == 5000


# ========== 代码契约（6 位 + 市场列） ==========

def test_codes_normalized_to_six_digits_with_market():
    """新浪返回带前缀代码；快照必须归一到 6 位，否则消费方精确匹配静默落空。"""
    raw = pd.DataFrame({
        "代码": ["sh600519", "sz000001", "bj920000", "600036"],
        "名称": ["贵州茅台", "平安银行", "安徽凤凰", "招商银行"],
        "最新价": [1442.66, 10.0, 10.29, 40.0],
    })
    out = rt.normalize_snapshot(raw)
    assert out["代码"].tolist() == ["600519", "000001", "920000", "600036"]
    assert out["市场"].tolist() == ["sh", "sz", "bj", "sh"]


def test_normalize_is_idempotent():
    once = rt.normalize_snapshot(pd.DataFrame({"代码": ["sh600519"], "名称": ["x"]}))
    twice = rt.normalize_snapshot(once)
    assert twice["代码"].tolist() == ["600519"]
    assert twice["市场"].tolist() == ["sh"]


def test_exact_six_digit_lookup_works():
    """run.py 取股票名、strategy/steps.py 取实时行都是 6 位精确匹配。"""
    df = rt.normalize_snapshot(_frame(5000, bj=100))
    assert len(df[df["代码"] == "600000"]) == 1, "6 位精确匹配失效=实时 enrichment 静默为空"
    assert int((df["市场"] == "bj").sum()) == 100


def test_tencent_and_sina_share_code_contract():
    """两源归一的代码形态必须一致（否则源切换时下游行为漂移）。"""
    tencent = rt.normalize_snapshot(rt.parse_tencent_quotes(TENCENT_LINE_600519))
    sina = rt.normalize_snapshot(pd.DataFrame({"代码": ["sh600519"]}))
    assert tencent["代码"].tolist() == sina["代码"].tolist() == ["600519"]
    assert tencent["市场"].tolist() == sina["市场"].tolist() == ["sh"]


# ========== 单一实现静态守卫 ==========

def _funcs_named(path: pathlib.Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]


def test_only_facade_and_delegation_define_get_realtime_quotes():
    """lib/data 下只允许 data_layer（委托）与 data_api（门面）定义该函数。"""
    owners = {p.name for p in LIB_DATA.glob("*.py") if _funcs_named(p, "get_realtime_quotes")}
    assert owners == {"data_layer.py", "data_api.py"}, f"又长出新的实时行情实现: {owners}"


def test_data_layer_delegates_instead_of_parsing():
    src = (LIB_DATA / "data_layer.py").read_text(encoding="utf-8")
    assert "get_market_snapshot" in src, "data_layer 未委托 data_realtime"
    assert "get_security_quotes" not in src.split("def get_market_sentiment")[0], \
        "data_layer 仍内置 pytdx 解析（第二套实现）"


def _non_doc_string_literals(path: pathlib.Path):
    """源码中的字符串字面量（排除 docstring）——注释/文档里提到 URL 不算实现。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            d = ast.get_docstring(node, clean=False)
            if d:
                docs.add(d)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value not in docs]


def test_no_second_tencent_parser():
    """除 data_realtime 外不得再有腾讯报文解析。

    判据取腾讯报文独有的 ``split('~')`` 标记 + 非 docstring 的 gtimg URL 字面量；
    不用泛化的 ``parts[`` 下标（多数解析器都用这个名字，会误伤）。
    ``host_health.py`` 是 host 注册表（只存 host 名，无请求与解析），豁免 URL 检查。
    """
    split_offenders, url_offenders = [], []
    for p in LIB_DATA.glob("*.py"):
        if p.name in ("data_realtime.py", "host_health.py"):
            continue
        if '.split("~")' in p.read_text(encoding="utf-8"):
            split_offenders.append(p.name)
        if any("gtimg" in s for s in _non_doc_string_literals(p)):
            url_offenders.append(p.name)
    assert split_offenders == [], f"腾讯报文解析存在第二份: {split_offenders}"
    assert url_offenders == [], f"腾讯行情 URL 在 data_realtime 之外被硬编码: {url_offenders}"


def test_host_health_is_registry_only():
    """豁免必须成立：host 注册表里不能出现请求/解析代码。"""
    src = (LIB_DATA / "host_health.py").read_text(encoding="utf-8")
    for banned in ("requests.get", '.split("~")', "resp.text"):
        assert banned not in src, f"host_health 越界做了取数/解析: {banned}"


def test_dead_hundred_row_implementation_is_gone():
    src = (LIB_DATA / "data_ashare.py").read_text(encoding="utf-8")
    assert "[:100]" not in src, "data_ashare 仍有「只取前 100 只」的截断"
    assert not _funcs_named(LIB_DATA / "data_ashare.py", "get_realtime_quotes")
    adata_src = (LIB_DATA / "data_adata.py").read_text(encoding="utf-8")
    assert not _funcs_named(LIB_DATA / "data_adata.py", "get_realtime_quotes")
    assert "list_market_current" not in adata_src, "adata 实时行情死代码未清"


def test_source_order_and_cache_key_contract():
    assert rt.SNAPSHOT_SOURCE_ORDER == ("sina_full", "tencent_batch", "pytdx_sample")
    assert rt.SNAPSHOT_CACHE_KEY == "realtime/realtime", "缓存 key 变更会让旧缓存/消费方失效"
    assert rt.SNAPSHOT_MIN_ROWS == 4000
    assert rt.TENCENT_BATCH_SIZE >= 200, "批量 10 码/请求是 20 倍放大，实测 200 码可行"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
