"""批次 A 安全加固的行为契约测试（ADR-0020）。

上午的安全/性能审查发现 5 项列入批次 A：P0-1 配置注入、P0-2 DOM XSS、
P1-1 错误上报、P1-4 dashboard 缓存串 preset、P2-1 路径基准。
本文件是这些修复的**机器判据**：修复若被回退、或同类问题以新形态出现，
这里必须判红。判据优先验证「结构性原因被消除」，而不是复述某个具体补丁。

对应报告：docs/audit/security-performance-review-2026-11-14.md
"""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

import config  # noqa: E402
from config import (  # noqa: E402
    PRESETS,
    SystemConfig,
    _CONFIG_SCHEMA,
    _RANGE_CONSTRAINTS,
    _SENSITIVE_SUFFIXES,
    get_config,
    public_config_dict,
    validate_config_overrides,
    write_config_overrides,
)
from lib.infra.api_core import api_key_is_valid  # noqa: E402
from routers.common import dashboard_cache_key  # noqa: E402


# ---------------------------------------------------------------- P0-1 配置注入

def test_schema_is_generated_from_dataclasses_not_handwritten():
    """根因判据：白名单必须覆盖每个 dataclass 字段，新增字段自动进入校验。

    旧实现手写 schema，只覆盖 4 个类 / 11 个字段，且有两个字段在 dataclass 里
    根本不存在 —— 靠人同步必然漂移。这里校验「除敏感后缀外零遗漏」。
    """
    for cls_name, fields in _CONFIG_SCHEMA.items():
        assert cls_name, "schema 不允许空类名"
    system_fields = {
        f.name for f in dataclasses.fields(SystemConfig)
        if not f.name.endswith(_SENSITIVE_SUFFIXES)
    }
    assert system_fields <= set(_CONFIG_SCHEMA["SystemConfig"]), (
        "SystemConfig 有字段没进热更新白名单（schema 由 dataclass 自动生成，"
        "出现遗漏说明生成逻辑被改坏了）"
    )


def test_range_constraints_keys_exist_in_schema():
    """范围约束是唯一手工登记的部分：键不存在即判红（替代「记得同步」）。"""
    for cls_name, field_name in _RANGE_CONSTRAINTS:
        assert cls_name in _CONFIG_SCHEMA, f"约束引用了未知类 {cls_name}"
        assert field_name in _CONFIG_SCHEMA[cls_name], (
            f"范围约束引用了不存在的字段 {cls_name}.{field_name}；"
            "旧 schema 里的 MLConfig.min_train_samples / CacheConfig.ttl_minutes 就是这种漂移"
        )


def test_sensitive_fields_are_never_writable_or_exported():
    """凭据 / 端点类字段既不进白名单，也不出现在导出结果里。"""
    sensitive = {
        f.name
        for f in dataclasses.fields(config.LLMConfig)
        if f.name.endswith(_SENSITIVE_SUFFIXES)
    }
    assert sensitive, "LLMConfig 应含敏感字段，否则本判据失去意义"
    assert not (sensitive & set(_CONFIG_SCHEMA.get("LLMConfig", {})))

    exported = json.dumps(public_config_dict(get_config("balanced")), ensure_ascii=False)
    for name in ("deepseek_api_key", "kimi_api_key", "mimo_api_key"):
        assert name not in exported, f"导出结果泄露了字段 {name}"


@pytest.mark.parametrize("payload", [
    {"risk": {"stop_loss_pct": "5.0"}},                 # 类型错
    {"risk": {"stop_loss_pct": 50.0}},                  # 范围外
    {"risk": {"max_hold_days": True}},                  # bool 冒充 int
    {"factor": {"factor_weights": {"x": "abc"}}},      # 非数值权重，旧实现会全站 500
    {"risk": {"nonexistent_field": 99.9}},             # 未知字段
    {"__proto__": {"polluted": True}},                  # 未知顶层键
    {"llm": {"deepseek_api_key": "attacker"}},         # 敏感字段
    {"risk": {"exit_days_map": {"A": float("nan")}}},  # NaN 进风控字典
])
def test_validate_config_overrides_rejects_malicious_payloads(payload):
    """写入侧必须严格拒绝，不允许静默 setattr 到全局配置。"""
    with pytest.raises(ValueError):
        validate_config_overrides(payload)


def test_validate_config_overrides_accepts_legal_value():
    out = validate_config_overrides({"risk": {"stop_loss_pct": 7.5}})
    assert out == {"risk": {"stop_loss_pct": 7.5}}


def test_export_import_roundtrip_is_self_consistent():
    """导出的每一份配置都必须能被写入侧接受，否则导出功能等于坏的。"""
    exported = public_config_dict(get_config("aggressive"))
    exported.pop("_preset", None)
    exported.pop("_exported_at", None)
    validated = validate_config_overrides(exported)
    assert "risk" in validated and "factor" in validated


def test_get_config_returns_isolated_copy():
    """PRESETS 共享单例不得被任一调用方永久污染（P0-1 第三层根因）。"""
    cfg = get_config("balanced")
    original = PRESETS["balanced"].risk.stop_loss_pct
    cfg.risk.stop_loss_pct = 19.0
    assert PRESETS["balanced"].risk.stop_loss_pct == original
    assert get_config("balanced").risk.stop_loss_pct == original
    assert get_config("aggressive").risk.stop_loss_pct != get_config("conservative").risk.stop_loss_pct


def test_write_config_overrides_is_atomic_and_shared_with_reader(tmp_path, monkeypatch):
    """写盘唯一入口：同目录临时文件 + os.replace，不残留 tmp，不写半截 JSON。"""
    target = tmp_path / "config.json"
    monkeypatch.setattr(config, "_CONFIG_FILE", target)
    written = write_config_overrides({"risk": {"stop_loss_pct": 6.0}})
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == written
    assert not (tmp_path / "config.json.tmp").exists()
    with pytest.raises(ValueError):
        write_config_overrides({"risk": {"stop_loss_pct": "oops"}})
    assert json.loads(target.read_text(encoding="utf-8")) == written, "非法写入不得破坏已有文件"


# ---------------------------------------------------------------- P1-4 缓存键

def test_dashboard_cache_key_isolates_non_default_presets():
    """缓存键必须纳入全部影响结果的参数；默认 preset 沿用历史键保持兼容。"""
    assert dashboard_cache_key("balanced") == "dashboard"
    assert dashboard_cache_key("aggressive") != dashboard_cache_key("conservative")
    assert "aggressive" in dashboard_cache_key("aggressive")


# ---------------------------------------------------------------- P1-1 错误上报

def _make_request(body: bytes, headers: dict | None = None):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/errors",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _post_error(body: bytes, headers=None):
    from fastapi import HTTPException
    from routers import system

    try:
        return asyncio.run(system.report_frontend_error(_make_request(body, headers)))
    except HTTPException as exc:
        return exc.status_code


def test_error_report_requires_valid_api_key(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", "batch-a-test-key")
    assert api_key_is_valid(None) is False
    assert api_key_is_valid("wrong") is False
    assert api_key_is_valid("batch-a-test-key") is True

    payload = json.dumps({"type": "javascript", "message": "boom"}).encode()
    assert _post_error(payload) == 401
    assert _post_error(payload, {"X-API-Key": "wrong"}) == 401


def test_error_report_bounds_and_field_whitelist(tmp_path, monkeypatch):
    import routers.system as system_mod

    monkeypatch.setenv("API_SECRET_KEY", "batch-a-test-key")
    monkeypatch.setattr(system_mod, "PROJECT_ROOT", tmp_path)
    good = {"X-API-Key": "batch-a-test-key"}

    assert _post_error(b"x" * 5000, good) == 413
    assert _post_error(b"not-json", good) == 400
    assert _post_error(json.dumps(["not", "an", "object"]).encode(), good) == 400

    payload = json.dumps({
        "type": "javascript",
        "message": "<img src=x onerror=alert(1)>",
        "path": "/dashboard",
        "injected": "must-not-be-persisted",
    }).encode()
    assert _post_error(payload, good) == {"status": "ok"}

    log = tmp_path / "cache" / "frontend_errors.jsonl"
    assert log.exists()
    record = json.loads(log.read_text(encoding="utf-8").strip())
    assert "injected" not in record
    assert record["message"] == "<img src=x onerror=alert(1)>"
    assert "url" not in record, "完整 URL 不得落盘"


# ---------------------------------------------------------------- P0-2 / P2-1 静态守卫

def test_no_duplicated_esc_definitions_in_renderers():
    """转义实现只能有一份；复制即漂移（exposures.js 曾定义却从不调用）。"""
    renderer_dir = ROOT / "static" / "js" / "renderers"
    offenders = [
        p.name for p in renderer_dir.glob("*.js")
        if re.search(r"(?m)^function esc\(", p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"发现重复 esc 实现：{offenders}"
    assert "export function esc(" in (renderer_dir / "utils.js").read_text(encoding="utf-8")


def test_vulnerable_renderers_do_not_assign_innerhtml():
    """innerHTML 只允许静态字面量；模板字符串 / 变量拼接一律判红。

    静态空态提示（如 '<tr>暂无数据</tr>'）不含外部数据，允许保留；
    判据盯的是「把外部数据交给 HTML 解释器」这个结构性原因。
    """
    for name in ("exposures.js", "positions.js"):
        text = (ROOT / "static" / "js" / "renderers" / name).read_text(encoding="utf-8")
        for match in re.finditer(r"\.innerHTML\s*=\s*([^;\n]+)", text):
            rhs = match.group(1).strip()
            assert rhs.startswith(("'", '"')), (
                f"{name} 存在非静态 innerHTML 赋值：{rhs[:60]}"
            )


def test_csp_no_unsafe_inline_for_scripts():
    core = (ROOT / "lib" / "infra" / "api_core.py").read_text(encoding="utf-8")
    assert "script-src 'self';" in core
    assert "script-src 'self' 'unsafe-inline'" not in core
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'http-equiv="Content-Security-Policy"' in html, "dev 直服时需要 meta CSP 兜底"


def test_routers_never_use_bare_file_parent_as_path_base():
    """RULES §2.2 P3 落地：routers/ 内禁止裸 Path(__file__).parent（P-63 复发点）。"""
    offenders = []
    for path in (ROOT / "routers").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "parent":
                continue
            value = node.value
            if (isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "Path"
                    and any(isinstance(a, ast.Name) and a.id == "__file__" for a in value.args)):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], f"发现裸路径基准：{offenders}（应统一走 PROJECT_ROOT）"
