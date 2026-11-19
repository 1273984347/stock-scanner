"""坑表 P-102 机器判据（2026-11-19）：权重**声明**即满足和为 1.0。

事故：真实盘后扫描每次都刷 `因子权重和不为 1.0（当前 1.0020），自动归一化`。
根因是声明错误——`strategy_weights` 六个 0.167 = 1.002；conservative 的
`factor_weights` 声明和甚至只有 0.40。运行期归一化把错声明掩盖了：
外部直接读 PRESETS 的调用方拿到的是未归一值，警告每次扫描刷屏。

修法：定义处 `_sum_one`，运行期兜底保留（仍处理 JSON 热覆盖等外部输入），
但内置预设不得再触发它。
"""
import copy
import logging

import pytest

import config as cfgmod
import factors


@pytest.mark.parametrize("preset", ["balanced", "aggressive", "conservative"])
def test_declared_weights_sum_to_one(preset):
    c = cfgmod.PRESETS[preset]
    assert sum(c.factor.factor_weights.values()) == pytest.approx(1.0, abs=1e-9), \
        f"{preset}.factor_weights 声明和不是 1.0"
    assert sum(c.factor.strategy_weights.values()) == pytest.approx(1.0, abs=1e-9), \
        f"{preset}.strategy_weights 声明和不是 1.0"


@pytest.mark.parametrize("preset", ["balanced", "aggressive", "conservative"])
def test_get_config_does_not_renormalize_builtin_presets(preset, caplog):
    """兜底判据不该被内置预设常态触发——那说明声明仍是错的。"""
    with caplog.at_level(logging.WARNING, logger="config"):
        c = cfgmod.get_config(preset)
    assert sum(c.factor.factor_weights.values()) == pytest.approx(1.0, abs=1e-9)
    assert "自动归一化" not in caplog.text, f"{preset} 仍在运行期归一化：声明未修"


def test_factor_module_default_weights_are_normalized():
    assert sum(factors.STRATEGY_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)


def test_runtime_guard_still_handles_external_bad_weights(monkeypatch, caplog):
    """兜底不能被删——config.json / 未来外部输入仍可能给出非归一权重。"""
    bad = copy.deepcopy(cfgmod.PRESETS["balanced"])
    bad.factor.factor_weights = {"a": 2.0, "b": 2.0}
    monkeypatch.setitem(cfgmod.PRESETS, "test_bad_weights", bad)

    with caplog.at_level(logging.WARNING, logger="config"):
        out = cfgmod.get_config("test_bad_weights")

    assert sum(out.factor.factor_weights.values()) == pytest.approx(1.0, abs=1e-9)
    assert "自动归一化" in caplog.text, "运行期兜底被误删"

    # get_config 深拷贝：坏权重不得污染 MONKEPATCH 进去的原对象
    assert sum(bad.factor.factor_weights.values()) == 4.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
