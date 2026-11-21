"""P-122/123/124 机器判据：data_sync --content 三项内容判据的自证测试。

P-10 纪律：尺子必须自证会判红——每个判据都注入已知违例形态并断言判红，
再断言合规仓判绿：

1. 升序判据（P-123）：注入降序文件 → 红；升序仓 → 绿；
2. schema 白名单（P-124）：注入厂商原始帧（stock_code/trade_time/…）→ 红；
3. volume 交叉校验（P-122）：注入 ×100（科创板实测形态）→ 红；
   轻微口径差（×3，带宽 [0.2,5] 内）→ 绿。

真实仓 2026-11-21 存量（610×100 / 2825 降序 / 17 厂商帧）预期判红，
修复路径见 docs/adr/0036-store-write-normalization.md。
"""
import importlib.util
import pathlib

import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_sync():
    spec = importlib.util.spec_from_file_location(
        "_data_sync_content", ROOT / "scripts" / "data_sync.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_parquet(root, dataset, code, df):
    d = root / dataset
    d.mkdir(parents=True, exist_ok=True)
    df.to_parquet(d / f"{code}.parquet", index=False)


def _good_frame(days=5, volume=1000.0, descending=False):
    """6 列标准形态；descending=True 时按日期降序排列（P-123 违例形态）。"""
    dates = pd.date_range("2026-11-10", periods=days)
    df = pd.DataFrame({
        "date": dates,
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
        "volume": [volume + i for i in range(days)],
    })
    if descending:
        df = df.iloc[::-1].reset_index(drop=True)
    return df


def _vendor_frame():
    """厂商原始帧（P-124 违例形态：13 列中英混排，无白名单登记）。"""
    dates = pd.date_range("2026-11-10", periods=5)
    return pd.DataFrame({
        "stock_code": ["600519"] * 5,
        "trade_time": dates,
        "date": dates,
        "open": 10.0, "close": 10.5, "high": 11.0, "low": 9.0,
        "volume": 1000.0, "amount": 10500.0,
        "change_pct": 0.5, "change": 0.05,
        "turnover_ratio": 1.0, "pre_close": 10.45,
    })


def test_wellformed_store_passes(tmp_path):
    """合规仓（升序 + 标准列集 + 两仓 volume 同量纲）三项判据全绿。"""
    root = tmp_path / "store"
    _write_parquet(root, "kline", "600519", _good_frame())
    _write_parquet(root, "kline_raw", "600519", _good_frame())
    assert _load_sync().check_content(root) == 0


def test_detects_descending_dates(tmp_path):
    """P-123：降序文件必须判红（tail() 语义反转的存量形态）。"""
    root = tmp_path / "store"
    _write_parquet(root, "kline", "600519", _good_frame(descending=True))
    _write_parquet(root, "kline_raw", "600519", _good_frame())
    assert _load_sync().check_content(root) == 1


def test_detects_mixed_order(tmp_path):
    """P-123：乱序（既非升序也非整体降序）同样判红。"""
    root = tmp_path / "store"
    df = _good_frame(days=6)
    shuffled = df.iloc[[0, 2, 1, 4, 3, 5]].reset_index(drop=True)
    _write_parquet(root, "kline", "600519", shuffled)
    _write_parquet(root, "kline_raw", "600519", _good_frame(days=6))
    assert _load_sync().check_content(root) == 1


def test_detects_vendor_raw_frame(tmp_path):
    """P-124：厂商原始帧（白名单外列集）必须判红。"""
    root = tmp_path / "store"
    _write_parquet(root, "kline", "600519", _vendor_frame())
    _write_parquet(root, "kline_raw", "600519", _good_frame())
    assert _load_sync().check_content(root) == 1


def test_detects_volume_x100(tmp_path):
    """P-122：×100（科创板实测形态：腾讯 qfq 对 688 给股、解析层再 ×100）判红。"""
    root = tmp_path / "store"
    _write_parquet(root, "kline", "688981", _good_frame(volume=1000.0 * 100))
    _write_parquet(root, "kline_raw", "688981", _good_frame(volume=1000.0))
    assert _load_sync().check_content(root) == 1


def test_volume_band_tolerates_minor_drift(tmp_path):
    """×3 在带宽 [0.2,5] 内不判红——只抓单位错，不抓源间轻微口径差。"""
    root = tmp_path / "store"
    _write_parquet(root, "kline", "600519", _good_frame(volume=3000.0))
    _write_parquet(root, "kline_raw", "600519", _good_frame(volume=1000.0))
    assert _load_sync().check_content(root) == 0


def test_missing_dataset_dir_is_skipped_not_fatal(tmp_path):
    """kline_raw 目录缺失时只比对存在的数据集，不误报为 volume 违例。"""
    root = tmp_path / "store"
    _write_parquet(root, "kline", "600519", _good_frame())
    assert _load_sync().check_content(root) == 0


# ---- Δ5 第 4 判据：复权口径交叉（2026-11-22）----

def _good_frame_n(n=150, close=10.5):
    """n 日标准帧（判据 4 需要 ≥ADJ_CHECK_MIN_COMMON 个公共日期）。"""
    dates = pd.date_range("2026-06-01", periods=n)
    return pd.DataFrame({
        "date": dates, "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": [1000.0 + i for i in range(n)],
    })


def test_adj_criterion_ignores_short_windows(tmp_path):
    """公共日期 < ADJ_CHECK_MIN_COMMON 时不判比值恒定——既有 5 日夹具形态。"""
    root = tmp_path / "store"
    _write_parquet(root, "kline", "600519", _good_frame())      # 5 日，同 close
    _write_parquet(root, "kline_raw", "600519", _good_frame())
    assert _load_sync().check_content(root) == 0


def test_adj_criterion_tolerates_few_constant_codes(tmp_path):
    """个别比值恒定 = 窗口内无除权事件（合法态）：3 只 ≤ max(5, 30%) 不判红。"""
    root = tmp_path / "store"
    for code in ("600001", "600002", "600003"):
        _write_parquet(root, "kline", code, _good_frame_n())
        _write_parquet(root, "kline_raw", code, _good_frame_n())
    assert _load_sync().check_content(root) == 0


def test_adj_criterion_detects_mass_pollution(tmp_path):
    """批量比值恒定 = raw 仓被前复权帧污染（源栈级中招是批量的）→ 判红。"""
    root = tmp_path / "store"
    for code in ("600001", "600002", "600003", "600004", "600005", "600006"):
        _write_parquet(root, "kline", code, _good_frame_n())
        _write_parquet(root, "kline_raw", code, _good_frame_n())  # 两仓内容相同
    assert _load_sync().check_content(root) == 1


def test_adj_criterion_passes_stepwise_ratio(tmp_path):
    """真前复权 vs 真不复权：close 比值随除权事件阶梯变化 → 不判红。"""
    root = tmp_path / "store"
    n = 150
    dates = pd.date_range("2026-06-01", periods=n)
    raw = _good_frame_n()
    qfq = raw.copy()
    qfq["close"] = [10.5 if i < n // 2 else 9.5 for i in range(n)]  # 一次除权阶梯
    _write_parquet(root, "kline", "600519", qfq)
    _write_parquet(root, "kline_raw", "600519", raw)
    assert _load_sync().check_content(root) == 0
