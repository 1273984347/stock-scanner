"""config 拆包判据（P1-6 二阶段，批次 C11，2026-11-20）

单文件 config.py（810 行 / 20 域 / 53 消费方）→ config/ 包（5 域文件 + 门面）。
门面契约三支柱：
1. **公共面保真**：包面 == v1 模块面 + 恰好 5 个子模块属性（契约文件
   docs/refactor/contracts/config-public-surface.json v2，双向比对零漂移）；
2. **晚绑定**：``monkeypatch.setattr(config, "_CONFIG_FILE", ...)`` 经包属性
   生效（_runtime 调用期读 ``import config as _pkg``，P-18/I4 同源纪律）；
3. **路径基准**（RULES §2.2）：config/ 包内 2 级 parent——``_env_file`` 指根 .env、
   ``ALERT_FALLBACK_DIR`` 默认指根 logs/alert_fallback。

53 个消费方 ``from config import X`` 零改动；行为等价由 L1/L2/L5 门禁背书。
"""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "docs" / "refactor" / "contracts" / "config-public-surface.json"


def _surface():
    import config

    return sorted(n for n in dir(config) if not n.startswith("__"))


def test_public_surface_exact_per_contract():
    """公共面双向比对：当前包面必须与契约 v2 逐名相等（防未来漂移）。"""
    doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
    current = _surface()
    assert current == sorted(doc["package_v2"]), (
        f"config 包面漂移：缺失={sorted(set(doc['package_v2']) - set(current))}, "
        f"新增={sorted(set(current) - set(doc['package_v2']))}；"
        f"新增公共名必须同步更新契约文件并说明理由")


def test_surface_delta_is_only_submodule_attrs():
    """相对 v1 的 delta 必须恰好是 5 个子模块属性（不许多删任何旧公共名）。"""
    doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
    v1 = set(doc["baseline_v1_module"])
    v2 = set(doc["package_v2"])
    assert v2 - v1 == {"_constants", "_env", "_presets", "_runtime", "_types"}
    assert v1 - v2 == set(), "拆包不得丢任何旧公共名"


def test_config_file_patch_takes_effect_via_late_binding(tmp_path, monkeypatch):
    """晚绑定核心判据：patch config._CONFIG_FILE（包属性）→ 读写都落 patched 路径。"""
    import config

    target = tmp_path / "overrides.json"
    monkeypatch.setattr(config, "_CONFIG_FILE", target)

    validated = config.write_config_overrides({"risk": {"stop_loss_pct": 7.5}})
    assert target.exists(), "写盘必须落在 patched 路径（包属性晚绑定）"
    assert validated["risk"]["stop_loss_pct"] == 7.5
    assert config.get_config("balanced").risk.stop_loss_pct == pytest.approx(7.5), (
        "get_config 必须经同一包属性读到覆盖")
    assert not (ROOT / "config.json").exists(), "仓库根 config.json 不得被误写"


def test_repo_paths_stay_at_root():
    """路径基准（RULES §2.2）：包内 2 级 parent——.env 与降级目录都指仓库根。"""
    import config

    assert config._env_file == ROOT / ".env"
    expected_fallback = os.environ.get(
        "ALERT_FALLBACK_DIR", str(ROOT / "logs" / "alert_fallback"))
    assert config.ALERT_FALLBACK_DIR == expected_fallback


def test_get_config_behavior_preserved():
    """行为等价抽检：默认值 / 未知 preset 回退 / 深拷贝隔离（批次 A P0-1 语义）。"""
    import config

    assert config.get_config("balanced").risk.stop_loss_pct == pytest.approx(5.0)
    fallback = config.get_config("nonexistent-preset")
    assert fallback.risk.stop_loss_pct == pytest.approx(5.0)

    cfg = config.get_config("balanced")
    cfg.factor.factor_weights["pollute"] = 999.0
    assert "pollute" not in config.PRESETS["balanced"].factor.factor_weights, (
        "get_config 返回值必须与 PRESETS 深拷贝隔离")


def test_presets_shape_and_normalization():
    """P-102 声明即归一：三 preset 因子权重和 == 1.0。"""
    import config

    assert set(config.PRESETS) == {"balanced", "aggressive", "conservative"}
    for name, cfg in config.PRESETS.items():
        total = sum(cfg.factor.factor_weights.values())
        assert abs(total - 1.0) <= 1e-9, f"{name} factor_weights 和 = {total}"


def test_env_derived_attrs_mirror_environ():
    """env 派生常量必须与 os.environ 动态一致（.env setdefault 已在 import 期生效）。"""
    import config

    assert config.FEISHU_APP_ID == os.environ.get("FEISHU_APP_ID", "")
    assert config.FEISHU_RECEIVE_ID_TYPE == os.environ.get(
        "FEISHU_RECEIVE_ID_TYPE", "open_id")
    assert config.TDX_PORT == int(os.environ.get("TDX_PORT", "7709"))


def test_load_order_env_before_types():
    """加载序判据：_env 先于 _types（LLMConfig 类定义期读 env）——
    import 期即断言 LLMConfig 的 env 默认值通道与 os.environ 一致。"""
    import config

    assert config.LLMConfig().deepseek_api_key == os.environ.get("DEEPSEEK_API_KEY", "")
    assert config.LLMConfig().api_base_url == (
        os.environ.get("LLM_API_BASE_URL")
        or os.environ.get("OLLAMA_API_BASE_URL")
        or os.environ.get("OLLAMA_BASE_URL", ""))
