"""同花顺 iFinD 数据适配器（ESG / 风险指标等结构化因子）

定位：非 K 线源，仅接入"时点/横截面结构化指标"——不进 data_source_registry
的 K 线优先级栈，不参与复权/成交量单位归一，避免污染回测口径（ADR-0023/0036）。

密钥：只读 skill 的 mcp_config.json（~/.workbuddy/skills/ifind-finance-data/），
不进仓库、不写日志；可用环境变量 IFIND_SKILL_DIR 覆盖 skill 路径。

延迟初始化：模块 import 期零网络调用（踩坑集"模块 import 期副作用"条款）。
限流：免费版 2 QPS，调用间隔 >=0.6s；带 TTL 缓存避免重复消耗权益。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKILL_DIR = Path(os.environ.get(
    "IFIND_SKILL_DIR",
    Path.home() / ".workbuddy" / "skills" / "ifind-finance-data",
))
_CALL_PY = _SKILL_DIR / "call.py"
_CONFIG_JSON = _SKILL_DIR / "mcp_config.json"
_PLACEHOLDER = "your ifind-mcp key"
_MIN_INTERVAL_S = 0.6  # 免费版 2 QPS
_CACHE_TTL_S = 6 * 60 * 60  # 6 小时；ESG 评级/风险指标为低频更新数据

_mod = None
_mod_lock = threading.Lock()
_last_call_ts = 0.0
_call_lock = threading.Lock()
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def _load_module():
    """延迟加载 skill 的 call.py（复用其 MCP 会话与工具白名单校验）"""
    global _mod
    if _mod is not None:
        return _mod
    with _mod_lock:
        if _mod is None:
            if not _CALL_PY.is_file():
                raise RuntimeError(f"iFinD 调用脚本不存在：{_CALL_PY}")
            spec = importlib.util.spec_from_file_location("_ifind_call", _CALL_PY)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _mod = module
    return _mod


def _read_token() -> str:
    if not _CONFIG_JSON.is_file():
        raise RuntimeError(f"iFinD 密钥配置不存在：{_CONFIG_JSON}")
    cfg = json.loads(_CONFIG_JSON.read_text(encoding="utf-8"))
    token = str(cfg.get("auth_token", "")).strip()
    if not token or token == _PLACEHOLDER:
        raise RuntimeError("iFinD 密钥未配置（占位符）")
    return token


def _throttled_call(server_type: str, tool_name: str, params: dict) -> dict:
    """带限流的单次调用。密钥在调用时读取，import 期不触碰。"""
    global _last_call_ts
    module = _load_module()
    _read_token()  # 前置校验，避免无效请求占用额度
    with _call_lock:
        wait = _MIN_INTERVAL_S - (time.monotonic() - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        res = module.call(server_type, tool_name, params)
        _last_call_ts = time.monotonic()
    if isinstance(res, dict) and res.get("ok") is False:
        raise RuntimeError(f"iFinD {tool_name} 调用失败：{res.get('error')}")
    return res


def _extract_answer(res: dict) -> str:
    """从 MCP 响应取 data.answer（iFinD 结构化指标统一返回 markdown 表格）"""
    data = res.get("data", {}) if isinstance(res, dict) else {}
    payload = data.get("result", data) if isinstance(data, dict) else {}
    if isinstance(payload, dict):
        for item in payload.get("content", []) or []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    parsed = json.loads(item["text"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(parsed, dict):
                    inner = parsed.get("data", parsed)
                    if isinstance(inner, dict) and isinstance(inner.get("answer"), str):
                        return inner["answer"]
    return ""


def _extract_params(res: dict) -> dict:
    """提取 indicators_params——指标口径元数据，必须随数据一起落库。"""
    data = res.get("data", {}) if isinstance(res, dict) else {}
    payload = data.get("result", data) if isinstance(data, dict) else {}
    if isinstance(payload, dict):
        for item in payload.get("content", []) or []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                try:
                    parsed = json.loads(item["text"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(parsed, dict):
                    inner = parsed.get("data", parsed)
                    if isinstance(inner, dict) and isinstance(inner.get("indicators_params"), dict):
                        return inner["indicators_params"]
    return {}


def _parse_table(answer: str) -> list[dict[str, str]]:
    """解析 markdown 表格为 dict 列表（按表头定位，不裸扫数值）"""
    lines = [ln for ln in answer.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def _cached(key: tuple, producer):
    """TTL 缓存；异常不缓存（下次可重试）"""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL_S:
            return hit[1]
    value = producer()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    """清空指标缓存（测试/手动刷新用）"""
    with _cache_lock:
        _cache.clear()


def _match_row(rows: list[dict[str, str]], code: str, col: str) -> dict[str, str] | None:
    """按代码匹配表格行；兼容 600519.SH / 600519 两种形态"""
    plain = code.split(".")[0]
    for row in rows:
        cell = next((v for k, v in row.items() if col in k), None)
        if cell and cell.split(".")[0] == plain:
            return row
    return None


def get_esg_factor(code: str) -> dict:
    """ESG 评级因子。

    Returns:
        dict: {rating, agency, code, source, indicators_params} 或 {error}
    """
    def producer():
        try:
            res = _throttled_call("stock", "get_esg_data", {
                "query": f"{code}的ESG评级"
            })
        except Exception as exc:  # noqa: BLE001 - 外部接口异常统一转 error dict
            return {"error": f"ifind esg failed: {exc}"}
        answer = _extract_answer(res)
        rows = _parse_table(answer)
        row = _match_row(rows, code, "代码")
        if not row:
            return {"error": f"no esg data for {code}"}
        rating_col = next((k for k in row if "ESG评级" in k), None)
        if not rating_col:
            return {"error": f"unexpected esg columns: {list(row)}"}
        agency = rating_col.split("ESG评级")[0] or "未知"
        return {
            "rating": row[rating_col].strip(),
            "agency": agency,
            "code": code,
            "source": "ifind/get_esg_data",
            "indicators_params": _extract_params(res),
        }

    return _cached(("esg", code), producer)


def get_risk_factor(code: str) -> dict:
    """风险定量指标因子（Sharpe / 区间最大回撤 / 波动率）。

    Returns:
        dict: {sharpe, max_drawdown_pct, volatility_pct, code, source,
               indicators_params} 或 {error}
    """
    def producer():
        try:
            res = _throttled_call("stock", "get_risk_indicators", {
                "query": f"{code}的夏普比率、最大回撤、波动率"
            })
        except Exception as exc:  # noqa: BLE001
            return {"error": f"ifind risk failed: {exc}"}
        answer = _extract_answer(res)
        rows = _parse_table(answer)
        row = _match_row(rows, code, "代码")
        if not row:
            return {"error": f"no risk data for {code}"}

        def find_num(keys: tuple[str, ...]) -> float | None:
            for k in row:
                if any(token in k for token in keys):
                    try:
                        return float(row[k].replace(",", ""))
                    except ValueError:
                        return None
            return None

        return {
            "sharpe": find_num(("Sharpe", "夏普")),
            "max_drawdown_pct": find_num(("最大回撤",)),
            "volatility_pct": find_num(("波动率",)),
            "code": code,
            "source": "ifind/get_risk_indicators",
            "indicators_params": _extract_params(res),
        }

    return _cached(("risk", code), producer)
