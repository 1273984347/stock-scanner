"""运行时域 —— get_config + JSON 热更新（读写校验/白名单/schema）+ offload 开关
（config 拆包 C11，2026-11-20，自 config.py :88-116 / :453-769 逐字搬迁）

⚠️ 晚绑定纪律（P-18 同源）：``_CONFIG_FILE`` 与 ``PRESETS`` 一律经
``import config as _pkg`` **调用期读包属性**——测试 patch ``config._CONFIG_FILE``
必须生效；模块级 from-import 会绑定拷贝，patch 失效。
"""
import copy
import dataclasses
import json
import logging
import math
import os
import re
import typing
from pathlib import Path
from typing import Dict

import config as _pkg  # 晚绑定：调用期访问包属性（sys.modules 中的部分初始化包）
from config._presets import PRESETS
from config._types import SystemConfig

logger = logging.getLogger(__name__)

# ---- RF-01 Phase 4：统一评分内核开关 ----------------------------------------
# P7-3（2026-11-12，ADR-0014）：灰度已收口，ScoringKernel 是唯一评分路径，
# FEATURE_UNIFIED_SCORING / unified_scoring_enabled 已删除；
# 数值口径由 L5 评分基线（9 入口 × 285 样本，容差 1e-9）继续锁定。

# ---- RF-01 Phase 6：异步 offload 开关（默认 all；回滚值 0，可按消费者灰度）----
# 取值（与已收口的评分灰度同构）：
#   0/off/false/no/""      -> 全部同步直调（回滚值，逐字节等价于改造前）
#   1/on/true/yes/all      -> 全部已接入消费者走线程池（**当前默认**）
#   "backtest,walkforward" -> 仅列出的消费者走线程池
# 默认值翻转（架构评审 P0-1，2026-11-20，docs/audit/architecture-review-2026-11-20.md
# 批次 C2）：Phase 6 期间默认 0 是回滚安全阀，灰度收口（37 迁移 + L9/L10 门禁）后
# 长期裸奔在事件循环上（/api/risk 冷路径整批 wall-time 阻塞冻结全站）。翻转前置条件：
# ① audit_logger._LOG_LOCK 真持锁 + WAL（并发 sqlite 写入）；② L9 真数据 off/on 对拍
# + L10 并发 P95 门禁 PASS。回滚：环境变量 **显式置 0**（删除变量 = 回到默认 all）。
# 判定只允许在 lib/infra/async_offload.py::run_blocking（RULES §1.7 T2）。
FEATURE_ASYNC_OFFLOAD_DEFAULT = "all"


def async_offload_enabled(consumer: str = "default") -> bool:
    """动态读环境变量，便于秒级切换；不依赖模块导入时的快照。"""
    raw = os.environ.get(
        "FEATURE_ASYNC_OFFLOAD", FEATURE_ASYNC_OFFLOAD_DEFAULT
    ).strip().lower()
    if raw in ("1", "true", "yes", "on", "all", "*"):
        return True
    if raw in ("", "0", "false", "no", "off", "none"):
        return False
    return consumer in {part.strip() for part in raw.split(",") if part.strip()}


def get_config(preset: str = "balanced") -> SystemConfig:
    """获取配置（带权重和验证，支持 JSON 文件覆盖）"""
    logger = logging.getLogger(__name__)
    if preset not in _pkg.PRESETS:
        logger.warning(f"未知preset: {preset}, 使用balanced")
    # 批次 A / P0-1：PRESETS 是模块级共享单例，直接返回会让任一调用方的修改
    # （含 _apply_overrides 的 setattr）永久污染全局口径，且跨 preset 串联。
    # 深拷贝切断共享状态；数值本身不变，L4/L5 口径不受影响。
    config = copy.deepcopy(_pkg.PRESETS.get(preset, _pkg.PRESETS["balanced"]))

    # 应用 JSON 覆盖（热更新：文件变化时自动重新加载）
    overrides = _load_json_overrides()
    if overrides:
        _apply_overrides(config, overrides)

    # 验证因子权重和
    total = sum(config.factor.factor_weights.values())
    if abs(total - 1.0) > 0.01:
        logger.warning(f"preset '{preset}' 因子权重和 = {total:.4f}（应为 1.0），自动归一化")
        config.factor.factor_weights = {k: v / total for k, v in config.factor.factor_weights.items()}

    return config


# ============================================================================
# JSON 配置文件热更新
# ============================================================================

_CONFIG_FILE = Path("config.json")
_config_mtime: float = 0
_cached_overrides: dict = {}

# ---------------------------------------------------------------------------
# 批次 A / P0-1 根因修：热更新白名单不再手工维护。
#
# 旧实现的手写 _CONFIG_SCHEMA 只覆盖 4 个类 / 11 个字段（其中 MLConfig.min_train_samples
# 与 CacheConfig.ttl_minutes 在 dataclass 里根本不存在），而 _apply_overrides 对
# 「字段存在但没登记进 schema」的情况仍然无条件 setattr ⇒ 任意 dict 可改写
# 风控 / 因子权重 / LLM 参数，并因 PRESETS 共享单例而永久污染。
# 根因不是「漏登记两个字段」，而是「白名单靠人同步」：新增字段必然再次漏登记。
# 现在白名单 = dataclass 字段集合（由类型标注自动推导），新增字段自动进入校验，
# 删除字段自动失去写入权；范围约束单独登记且由机器判据校验键必须存在。
# ---------------------------------------------------------------------------

# 需要限制取值范围的字段；(min, max) 为 None 表示不限。
_RANGE_CONSTRAINTS: dict = {
    ("RiskConfig", "stop_loss_pct"): (0.5, 20.0),
    ("RiskConfig", "take_profit_pct"): (1.0, 50.0),
    ("RiskConfig", "trailing_pct"): (0.1, 10.0),
    ("RiskConfig", "max_hold_days"): (1, 30),
    ("RiskConfig", "max_single_weight"): (0.01, 1.0),
    ("ScanConfig", "min_zt_days"): (0, 30),
    ("ScanConfig", "price_change_min"): (-10.0, 10.0),
    ("ScanConfig", "price_change_max"): (-10.0, 20.0),
}

# 凭据 / 请求端点类字段一律禁止热更新。按后缀匹配，新增同类字段自动被拒，
# 不需要有人记得把它加进黑名单。
_SENSITIVE_SUFFIXES = ("_api_key", "_secret", "_token", "_endpoint", "_base_url")

# 容器类值的上界，防止用超大结构撑爆内存或写盘。
_MAX_CONTAINER_ITEMS = 256
_MAX_STRING_LEN = 4096
_MAX_CONTAINER_DEPTH = 3


def _field_kind(annotation) -> str:
    """把类型标注映射成校验用的 kind 字符串。"""
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    origin = typing.get_origin(annotation)
    if annotation is list or origin is list:
        return "list"
    if annotation is dict or origin is dict:
        args = typing.get_args(annotation)
        # Dict[str, float] 是因子权重类字段：值必须是有限数值。
        # 旧漏洞正是 {"factor": {"factor_weights": {"x": "abc"}}} 让
        # sum(weights.values()) 抛 TypeError → 所有调用 get_config() 的端点 500。
        if len(args) == 2 and args[0] is str and args[1] is float:
            return "float_dict"
        return "dict"
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return "dataclass"
    return "any"


def _nested_dataclass(cls, field_name: str):
    """取嵌套 dataclass 字段的真实类型（供写入侧递归校验）。"""
    return typing.get_type_hints(cls).get(field_name)


def _build_config_schema(root_cls=SystemConfig) -> dict:
    """由 dataclass 字段集合生成 {类名: {字段名: (kind, min, max, desc)}}。"""
    schema: dict = {}

    def walk(cls) -> None:
        if cls.__name__ in schema:
            return
        hints = typing.get_type_hints(cls)
        fields: dict = {}
        for f in dataclasses.fields(cls):
            if f.name.endswith(_SENSITIVE_SUFFIXES):
                continue
            kind = _field_kind(hints.get(f.name, f.type))
            lo, hi = _RANGE_CONSTRAINTS.get((cls.__name__, f.name), (None, None))
            fields[f.name] = (kind, lo, hi, f.name)
            if kind == "dataclass":
                nested = _nested_dataclass(cls, f.name)
                if nested is not None:
                    walk(nested)
        schema[cls.__name__] = fields

    walk(root_cls)
    return schema


_CONFIG_SCHEMA = _build_config_schema()


def _validate_container(value, expected, depth: int = 0) -> bool:
    """递归校验 dict/list 的结构与规模，禁止 NaN/Inf 与超深嵌套。"""
    if depth > _MAX_CONTAINER_DEPTH:
        return False
    if expected is dict:
        if not isinstance(value, dict) or len(value) > _MAX_CONTAINER_ITEMS:
            return False
        return all(
            isinstance(k, str) and len(k) <= 128 and _validate_container(v, None, depth + 1)
            for k, v in value.items()
        )
    if expected is list:
        if not isinstance(value, list) or len(value) > _MAX_CONTAINER_ITEMS:
            return False
        return all(_validate_container(v, None, depth + 1) for v in value)
    if isinstance(value, dict):
        return _validate_container(value, dict, depth)
    if isinstance(value, list):
        return _validate_container(value, list, depth)
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, str) and len(value) > _MAX_STRING_LEN:
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        return True
    return False


def _validate_field_value(field_name: str, expected_type, value, min_val, max_val) -> bool:
    """按自动生成白名单的 kind 校验值。

    保留历史 5 参数签名；expected_type 现在是 kind 字符串（如 "float"/"dict"），
    兼容旧代码传入的 Python type 对象。
    """
    kind = expected_type
    if isinstance(kind, type):
        kind = {bool: "bool", int: "int", float: "float", str: "str"}.get(kind, "any")

    if kind == "dataclass":
        return isinstance(value, dict)
    if kind == "bool":
        if not isinstance(value, bool):
            return False
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(float(value)):
            return False
    elif kind == "str":
        if not isinstance(value, str) or len(value) > _MAX_STRING_LEN:
            return False
    elif kind == "float_dict":
        if not isinstance(value, dict) or len(value) > _MAX_CONTAINER_ITEMS:
            return False
        return all(
            isinstance(k, str) and len(k) <= 128
            and isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v))
            for k, v in value.items()
        )
    elif kind == "dict":
        return _validate_container(value, dict)
    elif kind == "list":
        return _validate_container(value, list)
    elif kind == "any":
        return _validate_container(value, None)
    else:
        return False

    if min_val is not None and value < min_val:
        return False
    if max_val is not None and value > max_val:
        return False
    return True


def validate_config_overrides(data: dict) -> dict:
    """写入侧严格校验：未知字段 / 非法值直接抛 ValueError（含路径），不静默丢弃。

    与读取侧 _apply_overrides 的「跳过非法值」不同：写入必须失败可见，
    否则用户会以为配置已生效。返回清洗后的 dict（只含白名单字段）。
    """
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")

    def walk(cls, obj: dict, path: str) -> dict:
        schema_root = _CONFIG_SCHEMA.get(cls.__name__, {})
        out: dict = {}
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            spec = schema_root.get(key)
            if spec is None:
                raise ValueError(f"字段 {here} 不允许热更新")
            kind, min_val, max_val, _desc = spec
            if kind == "dataclass":
                if not isinstance(value, dict):
                    raise ValueError(f"字段 {here} 必须是对象")
                nested = _nested_dataclass(cls, key)
                if nested is None:
                    raise ValueError(f"字段 {here} 缺少嵌套类型")
                out[key] = walk(nested, value, here)
            else:
                if not _validate_field_value(key, kind, value, min_val, max_val):
                    raise ValueError(f"字段 {here} 值不合法")
                out[key] = value
        return out

    return walk(SystemConfig, data, "")


def public_config_dict(cfg) -> dict:
    """导出侧白名单：与热更新白名单同一份 schema。

    保证 export → import 往返自洽，且不会把 LLM 长期密钥带出进程（P1-9 同源问题）。
    """
    schema_root = _CONFIG_SCHEMA.get(type(cfg).__name__, {})
    out: dict = {}
    for key, (kind, _lo, _hi, _desc) in schema_root.items():
        value = getattr(cfg, key, None)
        out[key] = public_config_dict(value) if kind == "dataclass" else copy.deepcopy(value)
    return out


def write_config_overrides(data: dict) -> dict:
    """配置写盘的唯一入口：严格校验 + 同目录临时文件 + os.replace 原子替换。

    读写共用 _CONFIG_FILE（**晚绑定**：经 _pkg 读包属性，测试 patch 生效），
    避免两侧落点分裂；非原子写会让热更新读到半截 JSON。
    """
    validated = validate_config_overrides(data)
    cfg_file = Path(_pkg._CONFIG_FILE)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_file.with_name(cfg_file.name + ".tmp")
    tmp.write_text(json.dumps(validated, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cfg_file)
    return validated


def _load_json_overrides() -> dict:
    """从 config.json 加载配置覆盖（带 mtime 缓存；_CONFIG_FILE 晚绑定见上）"""
    global _config_mtime, _cached_overrides

    cfg_file = Path(_pkg._CONFIG_FILE)
    if not cfg_file.exists():
        return {}

    try:
        mtime = cfg_file.stat().st_mtime
        if mtime == _config_mtime:
            return _cached_overrides

        with open(cfg_file, "r", encoding="utf-8") as f:
            _cached_overrides = json.load(f)
        _config_mtime = mtime
        logging.getLogger(__name__).info(f"已加载 config.json（{cfg_file}）")
        return _cached_overrides
    except Exception as e:
        logging.getLogger(__name__).warning(f"加载 config.json 失败: {e}")
        return {}


def _apply_overrides(cfg: SystemConfig, overrides: dict) -> None:
    """按自动生成白名单递归应用覆盖（读取侧兼容历史文件）。

    与写入侧的严格拒绝不同，这里对未登记字段 / 非法值一律跳过并记 warning，
    保证旧的 config.json 不会让服务起不来；写入侧由 validate_config_overrides 兜底。
    """
    schema_root = _CONFIG_SCHEMA.get(type(cfg).__name__, {})

    for key, value in overrides.items():
        spec = schema_root.get(key)
        if spec is None:
            logging.getLogger(__name__).warning(
                f"config.json 中的字段 {type(cfg).__name__}.{key} 不在热更新白名单，已跳过"
            )
            continue
        kind, min_val, max_val, _desc = spec
        if kind == "dataclass":
            if isinstance(value, dict):
                _apply_overrides(getattr(cfg, key), value)
            else:
                logging.getLogger(__name__).warning(
                    f"config.json 字段 {type(cfg).__name__}.{key} 应为对象，已跳过"
                )
            continue
        if not _validate_field_value(key, kind, value, min_val, max_val):
            logging.getLogger(__name__).warning(
                f"config.json 字段 {type(cfg).__name__}.{key} 值 {value!r} 不符合白名单 "
                f"(类型={kind}, 范围=[{min_val}, {max_val}])，保持原值"
            )
            continue
        setattr(cfg, key, value)
