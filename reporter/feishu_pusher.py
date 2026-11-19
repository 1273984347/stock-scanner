"""飞书推送器 —— 带 token 缓存 + 推送日志"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

from config import (
    FEISHU_APP_ID,
    FEISHU_APP_SECRET,
    FEISHU_RECEIVE_ID,
    FEISHU_RECEIVE_ID_TYPE,
)

logger = logging.getLogger(__name__)

# NOTE: _cached_token and _token_expire_at are module-level globals.
# Thread-unsafe: assumes single-threaded execution (e.g., a cron/scheduler process).
# If used in a multi-threaded context, wrap access in a threading.Lock.
_cached_token: str = ""
_token_expire_at: datetime = datetime.min

FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_IM_API = "https://open.feishu.cn/open-apis/im/v1/messages"
FEISHU_IMAGE_API = "https://open.feishu.cn/open-apis/im/v1/images"

# 推送心跳唯一真源（评审 P0-4，2026-11-20）：两条推送管线（本模块 + scan_notify）
# 都写这里，reporter/heartbeat.py 据此判定失联——复核 B5：废弃独立 stamp 方案
# （重复真源 + 春节 26h 固定阈值误报），以「末条成功推送时间 + 交易日历」判定。
PUSH_LOG_PATH = Path("cache/reports/push_log.json")


def _get_tenant_token(force_refresh: bool = False) -> str:
    """获取飞书 tenant token，带缓存."""
    global _cached_token, _token_expire_at

    now = datetime.now(timezone.utc)
    # 提前5分钟刷新
    refresh_before = timedelta(minutes=5)

    if not force_refresh and _cached_token:
        if now < _token_expire_at - refresh_before:
            return _cached_token

    try:
        resp = requests.post(
            FEISHU_TOKEN_URL,
            json={
                "app_id": FEISHU_APP_ID,
                "app_secret": FEISHU_APP_SECRET,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.error(f"获取飞书token失败: {data}")
            _cached_token = ""
            return ""
        _cached_token = data["tenant_access_token"]
        # token 有效期2小时，转换为本地 datetime
        expire_seconds = data.get("expire", 7200)
        _token_expire_at = now + timedelta(seconds=expire_seconds)
        logger.info("飞书token已刷新，有效期至 %s", _token_expire_at)
        return _cached_token
    except Exception as e:
        logger.error(f"获取飞书token异常: {e}")
        _cached_token = ""
        return ""


def _log_push_result(push_type: str, success: bool, detail: str = ""):
    """写入 PUSH_LOG_PATH，只保留最近100条。"""
    log_file = PUSH_LOG_PATH
    entries: List[dict] = []

    try:
        if log_file.exists():
            try:
                entries = json.loads(log_file.read_text(encoding="utf-8"))
            except Exception:
                entries = []
    except Exception as e:
        logger.warning("读取push_log失败: %s", e)

    entries.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": push_type,
        "success": success,
        "detail": detail,
    })

    # 只保留最近100条
    if len(entries) > 100:
        entries = entries[-100:]

    try:
        log_file.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("写入push_log失败: %s", e)


def log_push_result(push_type: str, success: bool, detail: str = "") -> None:
    """公开写入口：feishu_pusher 之外的推送管线（scan_notify）写同一心跳真源。

    评审 P0-4/P1-5（2026-11-20）：盘后扫描推送此前无任何日志——P-79 断流 6 个月
    的结构性根因之一；两条管线的**文本构造与发送路径保持独立**（勿合并），
    仅共享日志真源。
    """
    _log_push_result(push_type, success, detail)


def _upload_image(token: str, image_path: str) -> Optional[str]:
    """上传图片到飞书，返回 image_key。"""
    if not token or not os.path.exists(image_path):
        return None

    try:
        with open(image_path, "rb") as f:
            file_content = f.read()

        resp = requests.post(
            FEISHU_IMAGE_API,
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (os.path.basename(image_path), file_content, "image/png")},
            timeout=15,
        )
        data = resp.json()
        if data.get("code") == 0:
            return data.get("data", {}).get("image_key")
        logger.error("图片上传失败: %s", data)
        return None
    except Exception as e:
        logger.error("图片上传异常: %s", e)
        return None


def _send_text_message(token: str, text: str) -> bool:
    """发送纯文本消息。token失效时自动刷新重试一次。"""
    try:
        resp = requests.post(
            FEISHU_IM_API,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": FEISHU_RECEIVE_ID,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
            params={"receive_id_type": FEISHU_RECEIVE_ID_TYPE},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return True
        # 99991699 = token失效
        if data.get("code") == 99991699:
            new_token = _get_tenant_token(force_refresh=True)
            if new_token:
                resp = requests.post(
                    FEISHU_IM_API,
                    headers={"Authorization": f"Bearer {new_token}"},
                    json={
                        "receive_id": FEISHU_RECEIVE_ID,
                        "msg_type": "text",
                        "content": json.dumps({"text": text}),
                    },
                    params={"receive_id_type": FEISHU_RECEIVE_ID_TYPE},
                    timeout=10,
                )
                return resp.json().get("code") == 0
        logger.error("发送文本消息失败: %s", data)
        return False
    except Exception as e:
        logger.error("发送文本消息异常: %s", e)
        return False


def _send_image_message(token: str, image_key: str, text: str) -> bool:
    """发送图片+文字消息 (post类型)。token失效时自动刷新重试一次。"""
    content = {
        "zh_cn": {
            "title": "📈 量化报告",
            "content": [
                [{"tag": "text", "text": text}],
                [{"tag": "img", "image_key": image_key}],
            ],
        }
    }
    try:
        resp = requests.post(
            FEISHU_IM_API,
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": FEISHU_RECEIVE_ID,
                "msg_type": "post",
                "content": json.dumps(content),
            },
            params={"receive_id_type": FEISHU_RECEIVE_ID_TYPE},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return True
        if data.get("code") == 99991699:
            new_token = _get_tenant_token(force_refresh=True)
            if new_token:
                resp = requests.post(
                    FEISHU_IM_API,
                    headers={"Authorization": f"Bearer {new_token}"},
                    json={
                        "receive_id": FEISHU_RECEIVE_ID,
                        "msg_type": "post",
                        "content": json.dumps(content),
                    },
                    params={"receive_id_type": FEISHU_RECEIVE_ID_TYPE},
                    timeout=10,
                )
                return resp.json().get("code") == 0
        logger.error("发送图片消息失败: %s", data)
        return False
    except Exception as e:
        logger.error("发送图片消息异常: %s", e)
        return False


def push_report(text: str, chart_paths: List[str]) -> bool:
    """推送日报（图片+文字优先，失败降级为纯文本）。"""
    return _push("daily_report", text, chart_paths)


def push_morning_report(text: str, chart_paths: List[str]) -> bool:
    """推送早报（图片+文字优先，失败降级为纯文本）。"""
    return _push("morning_report", text, chart_paths)


def push_v4_report(scan_result: Dict, chart_paths: List[str] = None) -> bool:
    """推送 V4 报告（整合版）

    Args:
        scan_result: run_all_strategies() 的返回值
        chart_paths: 可选，图表路径列表

    Returns:
        推送是否成功
    """
    from reporter.report_generator import build_v4_dashboard_report, format_v4_report_text

    report = build_v4_dashboard_report(scan_result)
    text = format_v4_report_text(report)
    paths = chart_paths or []
    return _push("v4_report", text, paths)


def _push(push_type: str, text: str, chart_paths: List[str]) -> bool:
    """通用推送逻辑。"""
    token = _get_tenant_token()
    if not token:
        logger.warning("无法获取飞书token，打印文本: %s", text)
        print(text)
        _log_push_result(push_type, False, "no token")
        return False

    success = False
    fallback_text = False

    # 逐张上传图片
    for chart_path in chart_paths:
        if not os.path.exists(chart_path):
            logger.warning("图表文件不存在: %s", chart_path)
            continue
        image_key = _upload_image(token, chart_path)
        if image_key:
            ok = _send_image_message(token, image_key, text)
            if ok:
                success = True
                _log_push_result(push_type, True, f"image: {chart_path}")
            else:
                fallback_text = True
        else:
            fallback_text = True

        # 每张图发一条，避免单条消息过大
        if success:
            break

    # 降级：纯文本
    if fallback_text or (not chart_paths):
        ok = _send_text_message(token, text)
        if ok:
            success = True
            _log_push_result(push_type, True, "text_fallback")
        else:
            _log_push_result(push_type, False, "text_fallback_failed")

    return success
