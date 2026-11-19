"""配置包 —— 全局配置单一真相源（config 拆包 C11，2026-11-20）

自单文件 config.py（810 行 / ~20 配置域）拆分为域文件（评审 P1-6 二阶段）：

- ``_env.py``        环境域：.env 解析（三条护栏）+ env 派生常量 + 调用期访问器
- ``_types.py``      类型域：10 个域 dataclass + ``_sum_one``（P-102 声明即归一）
- ``_presets.py``    预设域：balanced / aggressive / conservative
- ``_runtime.py``    运行时域：get_config + JSON 热更新（自动 schema 白名单）+ offload 开关
- ``_constants.py``  常量域：交易成本/组合/打分卡/宏观门控散常量

**门面契约（机器判据 tests/test_config_package.py）**：
1. 公共面与拆包前**逐名相等**（86 名基线
   ``docs/refactor/contracts/config-public-surface.json``，含 3 个 .env 解析循环
   泄漏变量与 stdlib 泄漏——逐字节保真，不做"顺手清理"）；
2. ``config._CONFIG_FILE`` patch 经包属性晚绑定生效（_runtime 调用期读
   ``import config as _pkg``）；
3. 加载序 = env → types → presets → runtime → constants（LLMConfig 类定义期
   读 env，顺序不可换）。

53 个消费方 ``from config import X`` / ``import config`` 的用法零改动。
"""
import copy
import dataclasses
import json
import logging
import math
import os
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

# ---- 环境域（必须最先加载：LLMConfig 类定义期读 os.environ）----
from config._env import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_RECEIVE_ID,
    FEISHU_RECEIVE_ID_TYPE,
    TDX_DATA_PATH,
    TDX_HOST,
    TDX_PORT,
    TDX_REF_PATH,
    ALERT_FALLBACK_DIR,
    TRADING_DAYS_PER_YEAR,
    _ENV_KEY_RE,
    _parse_env_value,
    _env_file,
    _line,
    _k,
    _v,
    api_secret_key,
    tushare_token,
    stock_font_path_override,
)

# ---- 类型域 ----
from config._types import (
    FactorConfig,
    ScanConfig,
    RiskConfig,
    MLConfig,
    LLMConfig,
    PortfolioConfig,
    MonitorConfig,
    CacheConfig,
    BrokerConfig,
    SystemConfig,
    _sum_one,
)

# ---- 预设域 ----
from config._presets import PRESETS

# ---- 运行时域 ----
from config._runtime import (
    FEATURE_ASYNC_OFFLOAD_DEFAULT,
    async_offload_enabled,
    get_config,
    _CONFIG_FILE,
    _CONFIG_SCHEMA,
    _MAX_CONTAINER_DEPTH,
    _MAX_CONTAINER_ITEMS,
    _MAX_STRING_LEN,
    _RANGE_CONSTRAINTS,
    _SENSITIVE_SUFFIXES,
    _apply_overrides,
    _build_config_schema,
    _cached_overrides,
    _config_mtime,
    _field_kind,
    _load_json_overrides,
    _nested_dataclass,
    _validate_container,
    _validate_field_value,
    validate_config_overrides,
    public_config_dict,
    write_config_overrides,
)

# ---- 常量域 ----
from config._constants import (
    CACHE_RATE_LIMIT,
    DEFAULT_CORRELATION,
    RISK_AVERSION_CALIBRATED,
    RISK_AVERSION_UNCALIBRATED,
    MAX_POSITIONS,
    TURNOVER_PENALTY,
    COMMISSION_RATE,
    STAMP_DUTY_RATE,
    MIN_COMMISSION,
    SLIPPAGE_RATE,
    MARKET_CAP_LARGE,
    MARKET_CAP_MEDIUM,
    LAYER_WEIGHTS,
    SHIBOR_TIGHT_THRESHOLD,
    SHIBOR_CAUTION_THRESHOLD,
    IC_REDUNDANCY_THRESHOLD,
    HIGH_VOL_POSITION_SCALAR,
    MEDIUM_VOL_POSITION_SCALAR,
    LOW_VOL_POSITION_SCALAR,
    DEFAULT_STOP_LOSS,
    DEFAULT_TAKE_PROFIT,
)
