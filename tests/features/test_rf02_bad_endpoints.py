"""RF-02 契约快照 / 回归测试 —— 3 坏端点复原 + judges.json 漂移收口。

对应 ADR-0015 D1/D2/D3。RF-01 Phase 6 门禁纪律：D1/D2 改变响应内容，
必须契约快照对照 + 回归测试，证明「只复原、没改口径」。本文件即机器判据：
  * 响应形状（key 集合）与改造前契约一致；
  * 返回的是真实数据，而非永远空 / error 兜底；
  * judges.json 漂移源已删除，P0#13 禁用意图恢复。
"""

from __future__ import annotations

import json
import os

# routers.common 在导入期会启动 HS300 预取线程；测试必须零触网，先关掉。
os.environ.setdefault("SKIP_HS300_PREFETCH", "1")

from pathlib import Path  # noqa: E402

import lib.monitoring.notification_logger as nl  # noqa: E402
import lib.scoring.judge_panel as judge_panel  # noqa: E402
import routers.notifications as notifications_router  # noqa: E402
import routers.strategy as strategy_router  # noqa: E402


# ---------------------------------------------------------------- D1 notifications

def _seed_notifications(tmp_path: Path) -> None:
    d = tmp_path / "notifications"
    d.mkdir(parents=True, exist_ok=True)
    recs = [
        {"timestamp": "2026-11-13T10:00:00", "type": "signal", "title": "买入信号 A",
         "body": "置信度 0.9", "level": "info", "read": False},
        {"timestamp": "2026-11-13T11:00:00", "type": "alert", "title": "风险预警",
         "body": "回撤超限", "level": "warning", "read": False},
    ]
    (d / "notifications_20261113.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in recs), encoding="utf-8")
    nl.NOTIFICATION_DIR = d


def test_module_level_get_notifications_returns_real_data(tmp_path):
    """D1 根因：模块级 get_notifications 此前不存在 → 端点永远空兜底。"""
    _seed_notifications(tmp_path)
    out = nl.get_notifications(limit=100)
    assert len(out) == 2
    assert {r["title"] for r in out} == {"买入信号 A", "风险预警"}


def test_notifications_endpoint_shape_and_real_data(tmp_path):
    """契约快照：响应形状 {notifications,total,limit,offset} 不变 + 返回真实数据。"""
    _seed_notifications(tmp_path)
    res = notifications_router._compute_notifications(
        type=None, start_date=None, end_date=None, keyword=None, limit=50, offset=0)
    assert set(res) == {"notifications", "total", "limit", "offset"}
    assert res["total"] == 2
    assert len(res["notifications"]) == 2
    # 字段是真实 title/body，不是永不命中的 message/details
    assert all("title" in r and "body" in r for r in res["notifications"])


def test_notifications_keyword_search_hits_title_and_body(tmp_path):
    """D1 字段修复：检索从 message/details 改 title/body 后，关键词能命中。"""
    _seed_notifications(tmp_path)
    by_title = notifications_router._compute_notifications(None, None, None, "买入", 50, 0)
    assert by_title["total"] == 1 and by_title["notifications"][0]["title"] == "买入信号 A"
    by_body = notifications_router._compute_notifications(None, None, None, "回撤", 50, 0)
    assert by_body["total"] == 1 and by_body["notifications"][0]["title"] == "风险预警"
    miss = notifications_router._compute_notifications(None, None, None, "不存在的关键词", 50, 0)
    assert miss["total"] == 0


def test_notification_stats_real_totals(tmp_path):
    _seed_notifications(tmp_path)
    res = notifications_router._compute_notification_stats()
    assert set(res) == {"total", "by_type", "by_date", "recent"}
    assert res["total"] == 2
    assert res["by_type"] == {"signal": 1, "alert": 1}


# ---------------------------------------------------------------- D2 strategy/recommend

def test_strategy_recommend_restored_via_eval_current_weights(monkeypatch):
    """D2 契约快照：形状 {recommended:{weights,sharpe,max_dd,win_rate},symbols,note}
    不变；值来自真实存在的 eval_current_weights（不再 ImportError 兜底）。"""
    captured = {}

    def fake_eval(symbols, days=750, n_stocks=20):
        captured["symbols"] = symbols
        return {
            "weights": {"momentum": 0.3, "value": 0.7},
            "fitness": 0.42,
            "metrics": {"sharpe": 1.1, "max_dd": 0.2, "win_rate": 0.6},
        }

    monkeypatch.setattr(
        "lib.scoring.factor_optimizer.eval_current_weights", fake_eval)

    res = strategy_router._compute_strategy_recommend("600519,000858")
    assert set(res) == {"recommended", "symbols", "note"}
    assert res["recommended"] == {
        "weights": {"momentum": 0.3, "value": 0.7},
        "sharpe": 1.1, "max_dd": 0.2, "win_rate": 0.6,
    }
    assert res["symbols"] == ["600519", "000858"]
    assert captured["symbols"] == ["600519", "000858"]


def test_strategy_recommend_no_missing_factoroptimizer_import(monkeypatch):
    """D2 根因：原实现 import 不存在的 FactorOptimizer → 运行时 ImportError。
    修复后即使 eval 抛错，也返回结构化 error（而非 AttributeError/ImportError 兜底空）。
    错误契约保持 {recommended: None, error: ...}（与原端点 except 分支一致）。"""
    def fake_eval(symbols, days=750, n_stocks=20):
        raise RuntimeError("injected backtest failure")
    monkeypatch.setattr(
        "lib.scoring.factor_optimizer.eval_current_weights", fake_eval)

    res = strategy_router._compute_strategy_recommend("600519")
    assert "recommended" in res
    assert res["recommended"] is None
    assert "error" in res and "injected backtest failure" in res["error"]


# ---------------------------------------------------------------- D3 judges.json 漂移收口

def test_p0_13_judge_groups_remain_disabled():
    """D3 契约：6 组硬编码规则 weight=0（P0#13 禁用），AI量化=1.00 唯一启用。"""
    groups = judge_panel._load_judge_groups()
    disabled = ["经典价值", "成长", "宏观对冲", "技术趋势", "中国价投", "A股游资"]
    for g in disabled:
        assert groups[g]["weight"] == 0.0, f"{g} 应被 P0#13 禁用 (weight=0)"
    assert groups["AI 量化"]["weight"] == 1.00, "AI量化 应为唯一启用组 (weight=1.00)"


def test_judges_json_drift_file_is_gone():
    """D3 防回归：drift 源文件必须保持删除，否则 P0#13 意图会被静默抵消。"""
    assert not judge_panel._JUDGES_CONFIG_PATH.exists(), \
        "config/judges.json 已删除（RF-02 D3）；重新出现会重新启用被禁评委组"
