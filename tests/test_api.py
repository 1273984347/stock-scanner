"""API 端点测试

测试覆盖：
- 股票代码验证
- 安全响应头
- API Key 认证
- 端点参数校验
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    """每个测试自动设置 API 密钥"""
    monkeypatch.setenv("API_SECRET_KEY", "test-secret-key-for-unit-tests")


@pytest.fixture
def client():
    from api import app
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def auth_headers():
    return {"X-API-Key": "test-secret-key-for-unit-tests"}


# ========== 代码验证 ==========

class TestStockCodeValidation:
    def test_valid_6digit_code(self):
        from api import _validate_stock_code
        assert _validate_stock_code("000001") == "000001"
        assert _validate_stock_code("600519") == "600519"
        assert _validate_stock_code("300001") == "300001"

    def test_invalid_code_raises_400(self, client, auth_headers):
        resp = client.get("/api/score/abc", headers=auth_headers)
        assert resp.status_code == 400

    def test_short_code_raises_400(self, client, auth_headers):
        resp = client.get("/api/score/123", headers=auth_headers)
        assert resp.status_code == 400

    def test_long_code_raises_400(self, client, auth_headers):
        resp = client.get("/api/score/1234567", headers=auth_headers)
        assert resp.status_code == 400

    def test_empty_code_returns_404(self, client, auth_headers):
        # 空路径不会命中 /api/score/{code} 路由
        resp = client.get("/api/score/", headers=auth_headers)
        assert resp.status_code in (404, 405)


# ========== 安全响应头 ==========

class TestSecurityHeaders:
    def test_nosniff_header(self, client):
        resp = client.get("/")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_frame_deny(self, client):
        resp = client.get("/")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_xss_protection(self, client):
        resp = client.get("/")
        assert "X-XSS-Protection" in resp.headers

    def test_hsts_header(self, client):
        resp = client.get("/")
        assert "Strict-Transport-Security" in resp.headers

    def test_csp_header(self, client):
        resp = client.get("/")
        assert "Content-Security-Policy" in resp.headers


# ========== API Key 认证 ==========

class TestAPIKeyAuth:
    def test_missing_key_returns_401(self, client):
        resp = client.get("/api/market")
        assert resp.status_code == 401

    def test_wrong_key_returns_401(self, client):
        resp = client.get("/api/market", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_valid_key_passes_auth(self, client, auth_headers):
        # 通过认证后可能因数据源返回 500，但不应返回 401
        resp = client.get("/api/market", headers=auth_headers)
        assert resp.status_code != 401


# ========== Preset 参数校验 ==========

class TestPresetValidation:
    def test_invalid_preset_returns_400(self, client, auth_headers):
        resp = client.get("/api/scan?preset=invalid", headers=auth_headers)
        assert resp.status_code == 400

    def test_valid_presets_accepted(self, client, auth_headers):
        for preset in ("balanced", "aggressive", "conservative"):
            resp = client.get(f"/api/scan?preset={preset}", headers=auth_headers)
            # 可能因数据源返回 500 或超时，但不应返回 400
            assert resp.status_code != 400, f"preset={preset} should not return 400"


# ========== 根路由 ==========

class TestIndex:
    def test_index_returns_200_or_file(self, client):
        resp = client.get("/")
        assert resp.status_code in (200, 404)  # 200=html, 404=无static目录


# ========== 断路器 ==========

class TestCircuitBreaker:
    def test_circuit_breaker_import(self):
        from lib.cache import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10)
        assert cb.failure_threshold == 3
