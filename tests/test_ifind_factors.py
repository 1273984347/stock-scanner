"""iFinD ESG/风险因子接入契约测试

判据：
1. 默认关闭：compute_all_signals 不得触发 iFinD 网络调用（避免付费额度泄漏与 import 副作用）
2. 显式开启：因子进入 data_factors，且保留 indicators_params 口径元数据
3. 旁路性：新因子不改变 ic_score（评分口径冻结，S7）
4. 适配器：markdown 表格按列解析，不误抓数值里的小数（P-122 同类防回归）
5. 失败降级：外部源异常时返回 error dict，不抛到主流程
6. 开关数值判据：关闭态 ic_score 冻结；开启态精确 = 基线 + risk_score×0.10；缺一无效（只开一个开关）
7. 生产启用守卫：AST 扫描禁止把 use_ifind_risk_score 置 True（ADR-0037 已证伪）
"""

import ast
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import factors
from config import FactorConfig


def _hist(n=60):
    idx = pd.date_range("2026-01-01", periods=n, freq="B")
    close = pd.Series(np.linspace(10, 12, n), index=idx)
    return pd.DataFrame({
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": np.full(n, 1000.0),
    })


# 关闭态 ic_score 数值冻结（2026-11-27 实测，跨进程复跑一致）。
# L5 覆盖 9 个评分内核入口，但不经过 compute_all_signals，因此"评分口径零漂移"在这里兜底。
# 修改此值 = 改评分口径，必须走 ADR（S7）。
FROZEN_IC_SCORE_OFF = 0.2827


@pytest.fixture(autouse=True)
def _clear_cache():
    factors.clear_signals_cache()
    yield
    factors.clear_signals_cache()


def test_ic_score_frozen_when_flag_off():
    """关闭态评分口径冻结：任何非预期漂移直接判红（L5 不覆盖此路径）"""
    result = factors.compute_all_signals(_hist(), factor_config=FactorConfig(), code="600519")
    assert result["ic_score"] == FROZEN_IC_SCORE_OFF


# ---------- 1. 默认关闭：零网络调用 ----------
def test_default_off_no_network_call():
    cfg = FactorConfig()
    assert cfg.use_ifind_esg is False
    assert cfg.use_ifind_risk is False

    with patch("lib.data.data_ifind.get_esg_factor") as mock_esg, \
         patch("lib.data.data_ifind.get_risk_factor") as mock_risk:
        result = factors.compute_all_signals(_hist(), factor_config=cfg, code="600519")

    mock_esg.assert_not_called()
    mock_risk.assert_not_called()
    assert "ifind_esg" not in result.get("data_factors", {})
    assert "ifind_risk" not in result.get("data_factors", {})


# ---------- 2 & 3. 显式开启：因子入池 + 不改 ic_score ----------
def test_explicit_on_exposes_factors_without_changing_score():
    cfg = FactorConfig(use_ifind_esg=True, use_ifind_risk=True)
    esg = {"rating": "AAA", "agency": "华证", "indicators_params": {"华证ESG评级": {"截至日期": "最新"}}}
    risk = {"sharpe": 0.02, "max_drawdown_pct": 24.8, "volatility_pct": 3.2,
            "indicators_params": {"Sharpe": {"计算周期": "日"}}}

    hist = _hist()
    base = factors.compute_all_signals(hist, factor_config=FactorConfig(), code="600519")

    with patch("lib.data.data_ifind.get_esg_factor", return_value=esg) as mock_esg, \
         patch("lib.data.data_ifind.get_risk_factor", return_value=risk) as mock_risk:
        enabled = factors.compute_all_signals(hist, factor_config=cfg, code="600519")

    mock_esg.assert_called_once_with("600519")
    mock_risk.assert_called_once_with("600519")
    assert enabled["data_factors"]["ifind_esg"] == esg
    assert enabled["data_factors"]["ifind_risk"] == risk
    # 口径冻结：旁路因子不得改变评分
    assert enabled["ic_score"] == base["ic_score"]


# ---------- 4. 失败降级 ----------
def test_source_failure_does_not_break_pipeline():
    cfg = FactorConfig(use_ifind_esg=True, use_ifind_risk=True)
    with patch("lib.data.data_ifind.get_esg_factor", return_value={"error": "network down"}), \
         patch("lib.data.data_ifind.get_risk_factor", side_effect=RuntimeError("boom")):
        result = factors.compute_all_signals(_hist(), factor_config=cfg, code="600519")
    assert "ifind_esg" not in result["data_factors"]
    assert "ifind_risk" not in result["data_factors"]
    assert "ic_score" in result  # 主流程未崩


# ---------- 5. 适配器表格解析防回归 ----------
def test_adapter_parses_table_by_column_not_raw_number():
    from lib.data import data_ifind

    answer = (
        "|证券代码|证券简称|华证ESG评级|股息率|\n"
        "|---|---|---|---|\n"
        "|600519.SH|贵州茅台|AAA|3.774468|\n"
        "|002839.SZ|张家港行|B|4.814004|\n"
    )
    rows = data_ifind._parse_table(answer)
    assert len(rows) == 2
    assert rows[0]["证券代码"] == "600519.SH"
    # 股息率 4.814004 的小数部分不得被误当代码
    assert data_ifind._match_row(rows, "002839", "代码")["华证ESG评级"] == "B"


# ---------- 6. 风险评分加权（默认关闭 / Sharpe 优先 / 缺失不惩罚） ----------
def test_risk_score_default_off_does_not_change_score():
    cfg = FactorConfig(use_ifind_risk=True)  # 只暴露因子，不加权
    hist = _hist()
    risk = {"sharpe": 0.05, "max_drawdown_pct": 20.0}
    base = factors.compute_all_signals(hist, factor_config=FactorConfig(), code="600519")

    with patch("lib.data.data_ifind.get_risk_factor", return_value=risk):
        result = factors.compute_all_signals(hist, factor_config=cfg, code="600519")
    assert result["ic_score"] == base["ic_score"]
    assert result["data_factors"].get("_ifind_risk_score_enabled") is not True


def test_risk_score_sharpe_priority_and_capped():
    high = factors.calc_ifind_risk_score({"sharpe": 0.05, "max_drawdown_pct": 20.0})
    low = factors.calc_ifind_risk_score({"sharpe": -0.08, "max_drawdown_pct": 80.0})
    assert high is not None and low is not None
    assert high > 0 > low
    assert -1 <= high <= 1 and -1 <= low <= 1
    # Sharpe 优先：仅 Sharpe 高、回撤高时仍为正（权重 0.07 vs 回撤 0.03）
    mixed = factors.calc_ifind_risk_score({"sharpe": 0.05, "max_drawdown_pct": 80.0})
    assert mixed is not None and mixed > 0


def test_risk_score_missing_values_not_penalized():
    only_sharpe = factors.calc_ifind_risk_score({"sharpe": 0.05, "max_drawdown_pct": None})
    only_mdd = factors.calc_ifind_risk_score({"sharpe": None, "max_drawdown_pct": 20.0})
    both_missing = factors.calc_ifind_risk_score({"sharpe": None, "max_drawdown_pct": None})
    assert only_sharpe is not None and only_sharpe > 0
    assert only_mdd is not None and only_mdd > 0
    assert both_missing is None  # 缺失不参与，不惩罚


def test_risk_score_enabled_delta_exact():
    """开启态：ic_score 必须精确等于 基线 + risk_score × 0.10，而不是"变了就行"

    为什么不能用 `!=`：`!=` 只能证明"某处被改到了"，无法区分改动是否来自风险项、
    也测不出幅度漂移（权重从 0.10 变成 0.05 依然 `!=`）。L5 覆盖的是 9 个评分内核入口，
    而 `adjust_score_by_data_factors` 的唯一调用点在 `compute_all_signals`（factors.py:1711），
    L5 不经过该路径 —— 所以开关的数值判据只能落在这里。
    """
    risk = {"sharpe": 0.05, "max_drawdown_pct": 20.0}
    assert factors.calc_ifind_risk_score(risk) == 1.0  # 两个子项都顶到上限

    with patch("lib.data.data_ifind.get_risk_factor", return_value=risk):
        enabled = factors.compute_all_signals(
            _hist(),
            factor_config=FactorConfig(use_ifind_risk=True, use_ifind_risk_score=True),
            code="600519",
        )
    assert enabled["data_factors"].get("_ifind_risk_score_enabled") is True
    assert enabled["ic_score"] == round(FROZEN_IC_SCORE_OFF + 0.10, 4)


def test_risk_score_enabled_negative_delta_with_missing_sharpe():
    """Sharpe 缺失、回撤触顶：风险分为 −1.0，ic_score 精确 −0.10（缺失不惩罚的端到端判据）"""
    risk = {"sharpe": None, "max_drawdown_pct": 75.33}
    assert factors.calc_ifind_risk_score(risk) == -1.0

    with patch("lib.data.data_ifind.get_risk_factor", return_value=risk):
        enabled = factors.compute_all_signals(
            _hist(),
            factor_config=FactorConfig(use_ifind_risk=True, use_ifind_risk_score=True),
            code="600519",
        )
    assert enabled["ic_score"] == round(FROZEN_IC_SCORE_OFF - 0.10, 4)


def test_risk_score_flag_without_data_is_noop():
    """只开 use_ifind_risk_score、不开 use_ifind_risk：没有 ifind_risk 数据 ⇒ 与关闭态全等

    这是"缺一无效"的机器判据：防止未来只翻一个开关就以为加权生效了。
    """
    risk = {"sharpe": 0.05, "max_drawdown_pct": 20.0}
    with patch("lib.data.data_ifind.get_risk_factor", return_value=risk):
        result = factors.compute_all_signals(
            _hist(), factor_config=FactorConfig(use_ifind_risk_score=True), code="600519"
        )
    assert result["ic_score"] == FROZEN_IC_SCORE_OFF
    assert "_ifind_risk_score_enabled" not in result["data_factors"]


def test_adapter_reads_placeholder_as_error():
    from lib.data import data_ifind

    with patch.object(data_ifind, "_CONFIG_JSON", types.SimpleNamespace(
        is_file=lambda: True,
        read_text=lambda encoding=None: '{"auth_token": "your ifind-mcp key"}',
    )):
        with pytest.raises(RuntimeError, match="密钥未配置"):
            data_ifind._read_token()


def test_risk_score_never_enabled_in_production_code():
    """机器判据：生产模块不得把 use_ifind_risk_score 置 True

    ADR-0037 §决策2：严格前瞻两窗口方向翻转，加权已证伪；开关只作为可回滚实验入口保留。
    文档写"不得启用"不够——这里用 AST 扫生产代码，任何显式置 True 直接判红。
    """
    root = Path(__file__).resolve().parent.parent
    skip_dirs = {"tests", "scripts", "backups", "data", "docs", "node_modules",
                 "__pycache__", ".git", ".venv", "venv", "dist", "logs", "cache"}

    def _is_true(node) -> bool:
        return isinstance(node, ast.Constant) and node.value is True

    offenders = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = Path(dirpath) / fn
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if kw.arg == "use_ifind_risk_score" and _is_true(kw.value):
                            offenders.append(f"{p.relative_to(root)}:{node.lineno}")
                    # setattr(cfg, "use_ifind_risk_score", True) 同样算启用
                    if (isinstance(node.func, ast.Name) and node.func.id == "setattr"
                            and len(node.args) >= 3
                            and isinstance(node.args[1], ast.Constant)
                            and node.args[1].value == "use_ifind_risk_score"
                            and _is_true(node.args[2])):
                        offenders.append(f"{p.relative_to(root)}:{node.lineno}")
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    # 覆盖三种写法：x.attr = True / attr: bool = True（dataclass 默认值）/ 多目标赋值
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        name = target.attr if isinstance(target, ast.Attribute) else (
                            target.id if isinstance(target, ast.Name) else None)
                        if name == "use_ifind_risk_score" and node.value is not None and _is_true(node.value):
                            offenders.append(f"{p.relative_to(root)}:{node.lineno}")

    assert not offenders, f"生产代码启用了已证伪的评分开关: {offenders}"
