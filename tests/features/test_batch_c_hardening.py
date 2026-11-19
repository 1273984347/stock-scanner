"""批次 C 机器判据：供应链 / 运维 / 端点安全 / 暴露面（2026-11-15）。

对应审查报告 §2/§3 的 12 项（P2-10 CSV 公式注入因属 L9 严格冻结迁移、且触发
前提受限，未做，见 §14 遗留）。每条判据都验证「结构性原因被消除」。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- P2-14 .env

def test_c_env_value_parser_handles_quotes_and_inline_comments():
    from config import _parse_env_value
    assert _parse_env_value(" abc123 ") == "abc123"
    assert _parse_env_value(' "abc123" ') == "abc123"
    assert _parse_env_value("'abc123'") == "abc123"
    # 行内注释必须被截断，否则 `KEY=xxx # 备注` 会把注释当值 ⇒ 鉴权失败
    assert _parse_env_value("abc123 # 备注") == "abc123"
    # 引号内的 # 属于值本身
    assert _parse_env_value('"a # b"') == "a # b"


def test_c_env_keys_are_whitelisted():
    """空 key / 非法字符不得进入 os.environ（旧实现会抛 ValueError 让 import 崩掉）。"""
    # C11 拆包：.env 解析下沉到 config/_env.py（意图不变，路径随拆包更新）
    src = (ROOT / "config" / "_env.py").read_text(encoding="utf-8")
    assert "_ENV_KEY_RE" in src
    assert r"^[A-Z_][A-Z0-9_]*$" in src
    assert "setdefault(_k, _parse_env_value(_v))" in src


# ---------------------------------------------------------------- P1-2 WS

def test_c_ws_credentials_reads_header_and_subprotocol_only():
    """WS 凭据只从握手头取；URL query 回退必须消失。"""
    src = (ROOT / "api.py").read_text(encoding="utf-8")
    fn = src[src.index("def _ws_credentials"):src.index('@app.websocket("/ws")')]
    assert "query_params" not in fn, "WS 凭据不得再从 URL 读取"
    assert "authorization" in fn and "sec-websocket-protocol" in fn
    assert "urlsafe_b64decode" in fn, "子协议必须 base64url 解码"
    assert "websocket.query_params" not in src[src.index('@app.websocket("/ws")'):]

    import base64
    key = "s3cr3t-key_123"
    encoded = base64.urlsafe_b64encode(key.encode()).decode().rstrip("=")
    assert base64.urlsafe_b64decode((encoded + "=" * (-len(encoded) % 4)).encode()).decode() == key


# ---------------------------------------------------------------- P1-7 Zip Slip

def test_c_backup_restore_rejects_sibling_directory_and_oversize(tmp_path):
    from fastapi import HTTPException
    from routers.backup import _compute_backup_restore

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../restore_temp_evil/pwned.txt", "x")
    with pytest.raises(HTTPException) as exc:
        _compute_backup_restore(evil, tmp_path / "restore_temp", "evil.zip")
    assert exc.value.status_code == 400
    assert not (tmp_path / "restore_temp_evil").exists()

    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("ok.txt", "hello")
    out = _compute_backup_restore(good, tmp_path / "restore_temp", "good.zip")
    assert out["status"] == "restored"
    assert (tmp_path / "restore_temp" / "ok.txt").read_text() == "hello"


# ---------------------------------------------------------------- P2-7 5xx 回显

def test_c_5xx_does_not_echo_exception_text():
    src = (ROOT / "api.py").read_text(encoding="utf-8")
    body = src[src.index("async def _global_exception_handler"):src.index('@app.middleware("http")')]
    assert 'content={"detail": "内部服务器错误", "trace_id": trace_id}' in body
    assert "str(exc)[:200]" not in body
    assert "trace_id" in body


def test_c_scan_status_hides_raw_error():
    from lib.infra import scan_task_registry as r
    r.clear_tasks()
    r.start_task("balanced")
    r.fail_task("balanced", "Traceback: /secret/path/config.py line 42")
    task = r.get_task("balanced")
    assert task["status"] == "error"
    assert "/secret/path" not in json.dumps(task, ensure_ascii=False)
    assert task["error"] == "扫描失败（详情见服务端日志）"
    r.clear_tasks()


# ---------------------------------------------------------------- P2-8 暴露面

def test_c_docs_can_be_disabled_by_env():
    """ENABLE_API_DOCS=0 时 /docs、/redoc、/openapi.json 三个路由都不存在。"""
    code = (
        "import os;"
        "os.environ['ENABLE_API_DOCS']='0';"
        "os.environ['SKIP_HS300_PREFETCH']='1';"
        "import api;"
        "print([r.path for r in api.app.routes if 'openapi' in getattr(r,'path','') "
        "or getattr(r,'path','') in ('/docs','/redoc')])"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.strip().endswith("[]"), out.stdout


# ---------------------------------------------------------------- P2-9 ab-test 上界

def test_c_ab_test_has_bounds():
    src = (ROOT / "routers" / "strategy.py").read_text(encoding="utf-8")
    fn = src[src.index("async def ab_test_run"):src.index('@router.get("/api/ab-test/presets"')]
    assert "1 <= len(symbols) <= 20" in fn
    assert r'fullmatch(r"\d{6}", s)' in fn
    assert "60 <= days <= 1000" in fn
    assert "1 <= num_runs <= 20" in fn


# ---------------------------------------------------------------- P1-11 / P2-13

def test_c_requirements_have_upper_bounds_and_ml_packages():
    lines = [ln.strip() for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    pkgs = [ln for ln in lines if ">=" in ln]
    assert pkgs, "requirements 不应为空"
    for line in pkgs:
        assert "<" in line, f"缺少上界: {line}"
    text = "\n".join(lines)
    for name in ("xgboost", "lightgbm", "scikit-learn"):
        assert name in text, f"requirements 缺少 ML 依赖 {name}"


def test_c_scheduler_has_no_shell_true_and_escapes_xml():
    src = (ROOT / "scripts" / "scheduler" / "manage_paper_schedule.py").read_text(encoding="utf-8")
    assert "shell=True" not in src
    assert "xml.sax.saxutils" in src and "escape(" in src


def test_c_bat_files_use_script_relative_dir():
    for name in ("run_daily.bat", "scripts/paper_daily.bat"):
        text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
        assert "D:\\Claude Code" not in text, f"{name} 仍硬编码旧工作区路径"
        assert "%~dp0" in text, f"{name} 应使用脚本自身目录"
        assert "py -3.12" in text, f"{name} 应使用 py launcher 而非硬编码解释器路径"


def test_c_tdx_download_has_timeout_and_zip_guard():
    src = (ROOT / "scripts" / "update_tdx_data.py").read_text(encoding="utf-8")
    assert "urlretrieve(" not in src
    assert "urlopen(url, timeout=DOWNLOAD_TIMEOUT_S)" in src
    assert "is_relative_to" in src
    assert "MAX_ZIP_MEMBERS" in src and "MAX_EXTRACT_BYTES" in src


# ---------------------------------------------------------------- P2-11 postMessage

def test_c_postmessage_uses_explicit_origin_and_whitelist():
    src = (ROOT / "static" / "js" / "components" / "MultiWindow.js").read_text(encoding="utf-8")
    assert "postMessage({\n        type: 'init',\n        data: { code, name, ...data }\n      }, '*')" not in src
    assert src.count("'*'") <= 1, "不允许再用通配 targetOrigin"
    assert "event.origin !== window.location.origin" in src
    assert "ALLOWED_MESSAGE_TYPES" in src
