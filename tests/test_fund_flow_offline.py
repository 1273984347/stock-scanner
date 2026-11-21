"""N8（2026-11-22，增量评审）：离线 fundflow 缺失语义对齐 P-114。

评审依据：docs/audit/architecture-review-delta-2026-11-22.md N8——
``data_layer.get_stock_fund_flow`` 的离线分支在本地仓缺该码时返回
``{"main_net_ratio": 0, ...}`` 全零假中性，与 §6.2 P-114「不再用全零冒充
（0 值会以 1.0 分假中性进入四维加权，与真实中性不可区分）」直接冲突；
在线路径语义正确（FundFlowUnavailableError），仅离线分支破约定。
修复 = 离线缺失同样抛 FundFlowUnavailableError（消费端 strategies.py:274
已显式捕获 → 资金流维剔除并归一其余三维，T7-g 语义）。
"""
import pytest

pytestmark = pytest.mark.local_store

from lib.data import data_layer
from lib.data import local_store as ls
from lib.data.data_layer import FundFlowUnavailableError


@pytest.fixture(autouse=True)
def _offline_isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "STORE_ROOT", tmp_path / "store")
    monkeypatch.setattr(ls, "MANIFEST_PATH", tmp_path / "store" / "manifest.json")
    monkeypatch.setenv("QUANT_LOCAL_STORE", "on")
    monkeypatch.setenv("QUANT_OFFLINE", "1")
    with data_layer._fundflow_cache_lock:
        data_layer._fundflow_cache.clear()
    yield
    with data_layer._fundflow_cache_lock:
        data_layer._fundflow_cache.clear()


def test_offline_missing_raises_not_fake_neutral():
    """离线 + 仓内无该码 → 显式抛错（P-114 语义），绝不返回全零。"""
    with pytest.raises(FundFlowUnavailableError, match="offline"):
        data_layer.get_stock_fund_flow("600519")


def test_offline_present_returns_stored_value():
    """离线 + 仓内命中 → 返回真实存储值（本判据钉「离线可读」不被误伤）。"""
    ls.write_json("fundflow", "600519",
                  {"main_net_ratio": 0.05, "net_inflow_5d": 1.0})
    out = data_layer.get_stock_fund_flow("600519")
    assert out["main_net_ratio"] == 0.05
    assert out["net_inflow_5d"] == 1.0


def test_offline_missing_value_is_not_all_zero_dict():
    """返回值形态判据：缺失路径永不产出 {main_net_ratio: 0} 形态（P-114 根判词）。"""
    try:
        data_layer.get_stock_fund_flow("600519")
        raised = False
    except FundFlowUnavailableError:
        raised = True
    assert raised, "离线缺失必须抛错"
