"""API 横切层 —— 从 api.py 抽出的认证 / 校验 / 缓存 / 安全头 / 异常处理（RF-01 Phase 1 剩余）

为什么抽：api.py 的路由与这些横切关注点混在一起；拆分时它们必须**先**独立，
否则每个 router 都要各自实现一份认证/缓存/错误处理。

⚠️ 本模块的代码是从 api.py **逐字搬迁**的（只改了 CACHE_DIR 的路径基准）——
逐字搬迁是唯一不引入行为漂移的搬法（重写一遍必然出现细微差异）。

兼容：api.py 以同名 import 引用本模块的函数/常量
（`from lib.infra.api_core import verify_api_key, ...`）⇒ `from api import verify_api_key`
等旧用法继续可用，且**函数对象是同一个**。

⚠️ 路径基准注意：原实现里 `CACHE_DIR = Path(__file__).parent / "cache"`
（api.py 在仓库根）；本模块在 lib/ 下 ⇒ 必须用 `parent.parent` 才指向**同一个目录**，
否则缓存落盘位置改变 = 行为漂移。
"""

import json
import logging
import os

from config import api_secret_key
import re
import secrets
import threading
import zlib
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)


# ========== 安全常量 / 认证 ==========

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _get_api_key() -> str:
    """获取 API 密钥，密钥不存在则拒绝启动"""
    key = api_secret_key()  # P7-4：env 名归 config，调用时读取保 monkeypatch 语义
    if not key:
        raise RuntimeError(
            "API_SECRET_KEY must be set in production. "
            "Hint: export API_SECRET_KEY=your-secret-key"
        )
    return key


def api_key_is_valid(candidate: str | None) -> bool:
    """API Key 校验的唯一真源（常量时间比较）。

    批次 A / P1-1：/api/errors 需要在 handler 内校验凭据（不能挂 Security 依赖，
    否则 L2 契约快照与 L7 归属表的鉴权形状会变）；把比较逻辑收口到这里，
    避免两处各自实现一套、将其中一处漏改。
    """
    if not candidate:
        return False
    return secrets.compare_digest(candidate, _get_api_key())


async def verify_api_key(api_key: str = Security(API_KEY_HEADER)) -> str:
    """验证 API Key（生产模式：API_SECRET_KEY 必须设置）"""
    if api_key is None:
        raise HTTPException(status_code=401, detail="缺少API密钥")
    if not api_key_is_valid(api_key):
        raise HTTPException(status_code=401, detail="无效的API密钥")
    return api_key


def verify_ws_token(token: str) -> bool:
    """验证 WebSocket token"""
    expected = _get_api_key()
    return secrets.compare_digest(token, expected)


# ========== 输入校验 ==========

def _validate_stock_code(code: str) -> str:
    """Validate A-share stock code format (6 digits)"""
    if not re.match(r'^\d{6}$', code):
        raise HTTPException(status_code=400, detail=f"Invalid stock code format: {code}")
    return code


# ========== 安全中间件 ==========

async def add_security_headers(request: Request, call_next):
    """添加安全响应头"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # X-XSS-Protection 显式设为 "0" 禁用旧过滤器（避免引入漏洞），保留头以兼容老扫描器
    response.headers["X-XSS-Protection"] = "0"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # 批次 A / P0-2：移除 script-src 的 'unsafe-inline'（注入的 inline handler 不再执行）。
    # style-src 仍需 'unsafe-inline'：前端大量组件使用内联 style 属性。
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "object-src 'none'; base-uri 'self'"
    )
    return response


# ========== 全局异常处理 ==========

async def http_exception_handler(request: Request, exc: HTTPException):
    """全局 HTTPException handler（P1-3 修复 2026-06-05）。

    补充 5xx 错误的 trace_id logging（不改每处 except -> raise HTTPException）。
    保持原有 FastAPI 响应格式（detail 字段）。
    """
    if exc.status_code >= 500:
        logger.error(
            f"API 5xx: {request.method} {request.url.path} -> {exc.detail}",
            exc_info=True,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# ========== 缓存层 ==========

# ⚠️ 路径基准：原 api.py 在仓库根 -> "cache"；本模块在 lib/ -> 必须 parent.parent
CACHE_DIR = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# 缓存操作全局锁（保护 JSON 缓存文件的并发读写）
_cache_lock = threading.Lock()


def _cache_get(key: str, max_age: int = 3600):
    """读 JSON 缓存（线程安全，带随机 TTL 防止缓存雪崩，支持 gzip 压缩）"""
    import random, zlib
    path = CACHE_DIR / f"api_{key}.json"
    # 添加 ±10% 随机偏移防止缓存雪崩
    jitter = int(max_age * 0.1 * (random.random() * 2 - 1))
    effective_max_age = max(max_age + jitter, 1)
    with _cache_lock:
        if not path.exists():
            return None
        age = (datetime.now().timestamp() - path.stat().st_mtime)
        if age > effective_max_age:
            path.unlink(missing_ok=True)
            return None
        try:
            data = path.read_bytes()
            # 尝试 gzip 解压（向后兼容未压缩文件）
            try:
                data = zlib.decompress(data)
            except Exception:
                pass  # 未压缩的旧缓存文件
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.debug(f"缓存读取失败[{path.name}]: {e}")
            path.unlink(missing_ok=True)
            return None


def _cache_set(key: str, data):
    """写 JSON 缓存（线程安全，gzip 压缩）"""
    import zlib
    path = CACHE_DIR / f"api_{key}.json"
    with _cache_lock:
        try:
            json_data = json.dumps(data, ensure_ascii=False, default=str)
            # 压缩后存储（节省磁盘空间）
            compressed = zlib.compress(json_data.encode("utf-8"), level=6)
            # 只有压缩后更小才使用压缩
            if len(compressed) < len(json_data.encode("utf-8")):
                path.write_bytes(compressed)
            else:
                path.write_text(json_data, encoding="utf-8")
            try:
                # Windows 限制文件访问：仅当前用户可读写
                import os
                os.chmod(path, 0o600)
            except Exception:
                # 非 Windows 或权限不足时跳过
                pass
        except Exception as e:
            logger.warning(f"缓存写入失败[{key}]: {e}")
