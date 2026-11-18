"""批次 W：多 worker 部署守卫的机器判据。

覆盖三层：
  1. 探测逻辑（哪些环境变量、优先级、非法值）；
  2. 决策逻辑（默认拒绝 / opt-in 放行 / 单 worker 静默）；
  3. 装配守卫（确实挂在 api 的 startup 钩子上，而不是定义了没人调用）。

判据不依赖真实 fork 多进程——那是部署形态问题，代码只要能对「声明」做出
正确决策即可；真实多 worker 的架构约束由 README / ADR 文档承载。
"""

import inspect

import pytest

from lib.infra.worker_guard import (
    OPT_IN_ENV,
    WORKER_ENV_KEYS,
    declared_worker_count,
    verify_single_worker,
)


def _env(**overrides):
    """干净的测试环境：所有相关变量显式置空，避免宿主环境污染判据。"""
    env = {key: "" for key in WORKER_ENV_KEYS}
    env[OPT_IN_ENV] = ""
    env.update(overrides)
    return env


# --- 1. 探测逻辑 ---

def test_no_declaration_defaults_to_single_worker():
    assert declared_worker_count(_env()) == 1
    assert verify_single_worker(_env()) == 1


def test_web_concurrency_is_detected():
    assert declared_worker_count(_env(WEB_CONCURRENCY="4")) == 4


def test_uvicorn_workers_is_detected():
    assert declared_worker_count(_env(UVICORN_WORKERS="3")) == 3


def test_project_specific_key_takes_priority():
    env = _env(STOCK_SCANNER_WORKERS="2", UVICORN_WORKERS="5", WEB_CONCURRENCY="8")
    assert declared_worker_count(env) == 2


def test_invalid_and_zero_values_degrade_to_single():
    assert declared_worker_count(_env(WEB_CONCURRENCY="abc")) == 1
    assert declared_worker_count(_env(WEB_CONCURRENCY="0")) == 1
    assert declared_worker_count(_env(WEB_CONCURRENCY=" 2 ")) == 2  # 空白容忍


# --- 2. 决策逻辑 ---

def test_multi_worker_rejected_by_default_with_actionable_message():
    with pytest.raises(SystemExit) as exc:
        verify_single_worker(_env(WEB_CONCURRENCY="4"))
    message = str(exc.value)
    assert "多 worker" in message
    assert "单飞失效" in message          # 真实根因，不是「重复推送」
    assert "WEB_CONCURRENCY" in message
    assert "ALLOW_MULTI_WORKER" in message
    assert "uvicorn api:app --host 127.0.0.1" in message  # 给出可直接粘贴的修复命令


def test_explicit_opt_in_allows_but_warns(monkeypatch):
    """项目自定义 logging handler 不进 caplog，直接 spy logger 调用。"""
    import lib.infra.worker_guard as worker_guard
    warnings = []
    monkeypatch.setattr(worker_guard.logger, "warning", lambda msg: warnings.append(msg))

    env = _env(WEB_CONCURRENCY="4", ALLOW_MULTI_WORKER="1")
    assert verify_single_worker(env) == 4
    assert len(warnings) == 1
    assert "ALLOW_MULTI_WORKER" in warnings[0]
    assert "分片" in warnings[0]


def test_case_insensitive_opt_in():
    assert verify_single_worker(_env(WEB_CONCURRENCY="2", ALLOW_MULTI_WORKER="TRUE")) == 2


def test_single_worker_never_warns_or_exits(monkeypatch):
    """单 worker 是默认路径，不能产生任何噪音日志。"""
    import lib.infra.worker_guard as worker_guard
    calls = []
    monkeypatch.setattr(worker_guard.logger, "warning", lambda msg: calls.append(msg))
    monkeypatch.setattr(worker_guard.logger, "error", lambda msg: calls.append(msg))

    assert verify_single_worker(_env(WEB_CONCURRENCY="1")) == 1
    assert not calls


# --- 3. 装配守卫 ---

def test_guard_is_wired_into_api_startup():
    """函数存在但没挂到 startup = 空判据（坑表 P-56）。"""
    import api
    source = inspect.getsource(api)
    assert "verify_single_worker()" in source
    assert '_verify_single_worker_on_startup' in source
    assert '@app.on_event("startup")' in source
