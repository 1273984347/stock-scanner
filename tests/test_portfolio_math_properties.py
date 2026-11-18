"""P2 机器判据（2026-11-18）：组合数学的性质校验（零新增依赖）。

为什么不用 Riskfolio-Lib / PyPortfolioOpt 做「对拍」：
    引入它们要拖进 cvxpy 与求解器，而这两条口径本身有闭式解/定义式可钉：
    1) 对角协方差下 ERC 的解析解 w_i ∝ 1/σ_i（教科书结论，任何实现都一致）；
    2) BL 无观点时必须退化为市场均衡 Π = δ·Σ·w_mkt（模型定义本身）。
    性质判据比「和某个库某版本的数字一样」更稳——库升级不改变判据。
"""
import numpy as np
import pytest

from lib.portfolio.black_litterman import BLView, black_litterman
from lib.risk.risk_parity import risk_parity

GENERAL_COV = np.array([
    [0.04, 0.006, 0.002],
    [0.006, 0.09, 0.01],
    [0.002, 0.01, 0.16],
])


# ========== 风险平价（ERC） ==========

def _weights(result, assets):
    return np.array([result.weights[a] for a in assets])


def test_erc_weights_sum_to_one_and_non_negative():
    assets = ["A", "B", "C"]
    w = _weights(risk_parity(assets, GENERAL_COV), assets)
    assert w.sum() == pytest.approx(1.0, abs=1e-9)
    assert (w >= 0).all()


def test_erc_diagonal_closed_form_inverse_volatility():
    """对角协方差（不相关）时 ERC 有解析解 w_i ∝ 1/σ_i——这是外部库也会给同一答案的口径。"""
    sigmas = np.array([0.10, 0.20, 0.40])
    assets = [f"A{i}" for i in range(3)]
    result = risk_parity(assets, np.diag(sigmas ** 2))
    expected = (1 / sigmas) / (1 / sigmas).sum()
    assert np.allclose(_weights(result, assets), expected, atol=1e-6), \
        f"ERC 闭式解不符：{_weights(result, assets)} vs {expected}"


def test_erc_identical_assets_get_equal_weights():
    cov = np.full((3, 3), 0.04) + np.eye(3) * 0.01
    assets = ["A", "B", "C"]
    w = _weights(risk_parity(assets, cov), assets)
    assert np.allclose(w, 1 / 3, atol=1e-6)


def test_erc_equalizes_risk_contributions():
    assets = ["A", "B", "C"]
    result = risk_parity(assets, GENERAL_COV)
    rc = np.array([result.risk_contributions[a] for a in assets])
    assert rc.sum() == pytest.approx(1.0, abs=1e-6)
    assert rc.max() - rc.min() < 1e-3, f"风险贡献未等分：{rc}"


def test_erc_permutation_invariance():
    """资产顺序不该改变分配结果（只应跟着换位置）。"""
    assets = ["A", "B", "C"]
    w1 = _weights(risk_parity(assets, GENERAL_COV), assets)
    perm = [2, 0, 1]
    permuted = [assets[i] for i in perm]
    cov2 = GENERAL_COV[np.ix_(perm, perm)]
    w2 = _weights(risk_parity(permuted, cov2), permuted)
    assert np.allclose(w1[perm], w2, atol=1e-6)


def test_erc_total_risk_matches_quadratic_form():
    assets = ["A", "B", "C"]
    result = risk_parity(assets, GENERAL_COV)
    w = _weights(result, assets)
    assert result.total_risk == pytest.approx(float(np.sqrt(w @ GENERAL_COV @ w)), abs=1e-6)


def test_erc_zero_variance_asset_does_not_crash():
    """零波动资产是退化输入：允许权重收敛到它（零风险），但不得 NaN/负值。"""
    result = risk_parity(["A", "B"], np.array([[0.0, 0.0], [0.0, 0.04]]))
    w = np.array([result.weights["A"], result.weights["B"]])
    assert np.isfinite(w).all() and (w >= 0).all()
    assert w.sum() == pytest.approx(1.0, abs=1e-9)


# ========== Black-Litterman ==========

ASSETS = ["A", "B"]
COV = np.array([[0.04, 0.006], [0.006, 0.09]])
MKT = {"A": 0.6, "B": 0.4}
DELTA = 2.5


def test_bl_without_views_returns_equilibrium():
    r = black_litterman(ASSETS, COV, MKT)
    assert r.posterior_returns == r.equilibrium_returns
    assert r.posterior_weights == pytest.approx(MKT, abs=1e-9)
    assert r.view_returns == {}


def test_bl_equilibrium_matches_closed_form():
    """Π = δ·Σ·w_mkt 是模型定义式，任何库实现都相同。"""
    r = black_litterman(ASSETS, COV, MKT)
    pi = DELTA * COV @ np.array([0.6, 0.4])
    assert [r.equilibrium_returns[a] for a in ASSETS] == pytest.approx(list(pi), abs=1e-9)


def test_bl_confidence_monotonic_toward_view():
    """置信度越高，后验越靠近观点（否则 Omega 的构造就是反的）。"""
    pi_a = black_litterman(ASSETS, COV, MKT).equilibrium_returns["A"]
    target = pi_a + 0.10
    gaps = []
    for conf in (0.1, 0.5, 0.9):
        r = black_litterman(ASSETS, COV, MKT, [BLView(["A"], [1.0], target, conf)])
        gaps.append(abs(r.posterior_returns["A"] - target))
    assert gaps[0] > gaps[1] > gaps[2], f"置信度单调性失效：{gaps}"
    assert gaps[2] < 0.02, "高置信度下后验几乎没动，Omega 量级可疑"


def test_bl_bearish_view_moves_posterior_down():
    pi_a = black_litterman(ASSETS, COV, MKT).equilibrium_returns["A"]
    r = black_litterman(ASSETS, COV, MKT, [BLView(["A"], [1.0], pi_a - 0.10, 0.9)])
    assert r.posterior_returns["A"] < pi_a


def test_bl_posterior_weights_normalized_and_non_negative():
    r = black_litterman(ASSETS, COV, MKT, [BLView(["A"], [1.0], 0.2, 0.8)])
    w = np.array([r.posterior_weights[a] for a in ASSETS])
    assert (w >= 0).all()
    assert w.sum() == pytest.approx(1.0, abs=1e-9)


def test_bl_bullish_view_shifts_weight_toward_asset():
    base = black_litterman(ASSETS, COV, MKT).posterior_weights["A"]
    pi_a = black_litterman(ASSETS, COV, MKT).equilibrium_returns["A"]
    r = black_litterman(ASSETS, COV, MKT, [BLView(["A"], [1.0], pi_a + 0.20, 0.9)])
    assert r.posterior_weights["A"] > base, "看多观点没有提高该资产权重"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
