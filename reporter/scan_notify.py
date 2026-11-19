"""盘后选股信号的飞书推送与控制台输出 —— RF-01 Phase 7 P7-2 自 notifier.py 平迁。

逐字搬迁自 notifier.py（原模块已删除，BC-08，见 docs/refactor/MIGRATION-GUIDE.md）：
函数体零改动，payload 构造与 HTTP 发送路径与原实现完全一致；
仅去掉模块顶部的 DeprecationWarning 与未使用的 `get_realtime_quotes` import。

注意：本模块与 `reporter.feishu_pusher` 是**两条独立推送管线**（后者带 token 缓存 /
图片上传 / push_log / 降级打印）。合并会改变推送行为，勿顺手合并。
"""

import json
import logging
import time
from datetime import datetime

import pandas as pd
import requests

from config import (FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_RECEIVE_ID,
                    FEISHU_RECEIVE_ID_TYPE, get_config)
from factors import evaluate_macro_gate
# P1-6（2026-11-20）：直连真源 lib.data.data_layer——经 scanner 门面会构成
# scanner→pipeline→本模块→scanner 三节点环（评审 V9）
from lib.data.data_layer import get_market_sentiment

logger = logging.getLogger(__name__)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


def _check_required_env(key: str, name: str):
    """检查必需的环境变量是否配置"""
    val = key
    if not val:
        logger.warning(f"{name}未配置，部分功能不可用")
        return None
    return val


# 飞书配置检查
_feishu_app_id = _check_required_env(FEISHU_APP_ID, "FEISHU_APP_ID")
_feishu_app_secret = _check_required_env(FEISHU_APP_SECRET, "FEISHU_APP_SECRET")
_feishu_configured = _feishu_app_id and _feishu_app_secret


def get_tenant_token() -> str:
    if not _feishu_configured:
        logger.error("飞书配置不完整，跳过获取token")
        return ""
    resp = requests.post(TOKEN_URL, json={
        "app_id": _feishu_app_id,
        "app_secret": _feishu_app_secret
    }, timeout=10)
    data = resp.json()
    if data.get("code") == 0:
        return data["tenant_access_token"]
    logger.error(f"获取 token 失败: {data}")
    return ""


def format_stock_table(df: pd.DataFrame, strategy_name: str) -> str:
    if df.empty:
        return ""
    lines = [f"【{strategy_name}】{len(df)} 只", "-" * 40]
    cols = [c for c in df.columns if c != "策略"]
    for _, row in df.iterrows():
        parts = []
        for c in cols:
            val = row[c]
            if isinstance(val, float):
                parts.append(f"{val:.2f}")
            else:
                parts.append(str(val))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def build_message_content(results: dict, preset: str = "balanced") -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    config = get_config(preset)
    risk = config.risk

    # 获取市场概况
    sentiment = get_market_sentiment()
    macro_gate = evaluate_macro_gate(sentiment)

    # 统计各策略候选股数量（不暴露具体持仓明细）
    strategy_counts = {}
    total = 0
    for name, df in results.items():
        if not df.empty:
            strategy_counts[name] = len(df)
            total += len(df)

    parts = [
        f"盘后选股信号 ({now})",
        f"策略: {preset}",
        "=" * 30,
        "",
        "【市场概况】",
        f"  情绪分数: {sentiment:.2f}",
        f"  宏观门控: {macro_gate.state}",
        f"  仓位乘数: {macro_gate.position_multiplier:.0%}",
        f"  说明: {macro_gate.reasoning}",
        "",
        "=" * 30,
        "",
        "【候选股统计】",
    ]

    # 只显示策略和数量，不暴露具体股票明细
    for name, count in strategy_counts.items():
        parts.append(f"  {name}: {count} 只")

    if total == 0:
        parts.append("  今日无符合条件的候选股")
    else:
        parts.append(f"  共 {total} 只候选股")

    parts.append("")
    parts.append("=" * 30)
    parts.append("风控规则：")
    parts.append(f"  止损: -{risk.stop_loss_pct}%")
    parts.append(f"  止盈: +{risk.take_profit_pct}%")
    if risk.trailing_stop:
        parts.append(f"  移动止盈: 回撤 {risk.trailing_pct}%")
    parts.append(f"  最大持仓: {risk.max_hold_days} 天")
    parts.append("")
    parts.append("仅供参考，不构成投资建议。")

    return "\n".join(parts)


# 推送重试退避（评审 P0-4/P1-5，2026-11-20）：第 1/2 次失败后分别等待 1s/2s。
# 坑表 P-81：pytest 把全局 time.sleep patch 成 no-op，判据只数尝试次数不依赖等待语义。
RETRY_BACKOFF_S = (1.0, 2.0)


def send_feishu_message(results: dict, preset: str = "balanced",
                        max_attempts: int = 3) -> bool:
    """盘后选股信号推送。

    评审 P0-4/P1-5（2026-11-20）改造：
    - **3 次退避重试**（旧实现单次 POST，偶发网络抖动即失败且不可知）；
    - 最终结果写 push_log（``feishu_pusher.log_push_result``，与晨报/策略报告同一
      真源）——心跳哨兵（``reporter/heartbeat.py``）据此判定失联。P-79 的教训：
      本管线断流 6 个月的根因之一是**无任何日志**。
    文本构造与发送路径仍与本模块 ``send_alert_message`` 独立（两条管线勿合并）。
    """
    if not results:
        logger.info("无结果，跳过推送")
        return True
    if not FEISHU_RECEIVE_ID:
        logger.error("FEISHU_RECEIVE_ID 未配置")
        return False
    token = get_tenant_token()
    if not token:
        from reporter.feishu_pusher import log_push_result
        log_push_result("scan_notify", False, "获取 token 失败")
        return False
    content = build_message_content(results, preset)
    body = {
        "receive_id": FEISHU_RECEIVE_ID,
        "msg_type": "text",
        "content": json.dumps({"text": content}, ensure_ascii=False)
    }
    ok = False
    detail = ""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(MESSAGE_URL, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8"
            }, params={"receive_id_type": FEISHU_RECEIVE_ID_TYPE},
                json=body, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                logger.info("飞书推送成功（第 %d/%d 次尝试）", attempt, max_attempts)
                ok = True
                detail = "ok"
                break
            detail = f"飞书返回错误: {data}"
            logger.error("飞书推送失败（第 %d/%d 次）: %s", attempt, max_attempts, data)
        except Exception as e:
            detail = f"飞书推送异常: {e}"
            logger.error("飞书推送异常（第 %d/%d 次）: %s", attempt, max_attempts, e)
        if attempt < max_attempts:
            time.sleep(RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)])
    from reporter.feishu_pusher import log_push_result
    log_push_result("scan_notify", ok, detail[:200])
    return ok


from lib.monitoring.alert_content import build_alert_content  # noqa: F401 —— 兼容 re-export（实现已下沉，P1-6）


def send_alert_message(alert_type: str, alerts: list) -> bool:
    """告警类消息的飞书推送（与 send_feishu_message 并列的独立入口）。

    发送管线与 send_feishu_message 相同（token + POST），有意不复用/不抽取——
    后者函数体无测试覆盖（HTTP 路径），保持零改动降低回归面；两处文本构造
    形状不同（策略统计 vs 告警明细），不满足合并条件。
    """
    if not FEISHU_RECEIVE_ID:
        logger.error("FEISHU_RECEIVE_ID 未配置")
        return False
    token = get_tenant_token()
    if not token:
        return False
    content = build_alert_content(alert_type, alerts)
    body = {
        "receive_id": FEISHU_RECEIVE_ID,
        "msg_type": "text",
        "content": json.dumps({"text": content}, ensure_ascii=False)
    }
    try:
        resp = requests.post(MESSAGE_URL, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }, params={"receive_id_type": FEISHU_RECEIVE_ID_TYPE},
            json=body, timeout=10)
        data = resp.json()
        if data.get("code") == 0:
            logger.info("飞书告警推送成功: %s", alert_type)
            return True
        logger.error(f"飞书告警返回错误: {data}")
        return False
    except Exception as e:
        logger.error(f"飞书告警推送异常: {e}")
        return False


def unwrap_scan_results(scan_result):
    """P-79（2026-11-14）：把 `pipeline.run_all_strategies` 的返回值规整为裸 {策略名: DataFrame}。

    pipeline 返回的是复合 scan_result（results/risk_check/portfolio/ml_ranking/...），
    本模块的 print_results / send_feishu_message 只消费其中的 "results" 键
    （与 routers/common.py 对 Web 路径的解包一致）。2026-05-30 起 run.py 把复合
    dict 直接传进来，有候选股即崩（'dict' object has no attribute 'drop'），
    控制台展示与飞书推送断流 6 个月（push_log.json 末条 2026-05-30 佐证）。

    兼容三种输入：
    - 复合 scan_result（含 "results" dict 键）→ 取 "results"
    - 裸映射（历史调用方直接传 {策略: DataFrame}）→ 原样返回
    - 非 dict（None 等）→ 空映射
    """
    if isinstance(scan_result, dict) and isinstance(scan_result.get("results"), dict):
        return scan_result["results"]
    if isinstance(scan_result, dict):
        return scan_result
    return {}


def print_results(results: dict, preset: str = "balanced"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    config = get_config(preset)
    sentiment = get_market_sentiment()
    macro_gate = evaluate_macro_gate(sentiment)

    print(f"\n{'='*60}")
    print(f"  盘后选股信号 ({now})")
    print(f"  策略: {preset}")
    print(f"{'='*60}")
    print(f"\n【市场概况】")
    print(f"  情绪分数: {sentiment:.2f}")
    print(f"  宏观门控: {macro_gate.state} ({macro_gate.regime})")
    print(f"  仓位乘数: {macro_gate.position_multiplier:.0%}")
    print(f"  说明: {macro_gate.reasoning}")

    total = 0
    for name, df in results.items():
        print(f"\n【{name}】{len(df)} 只")
        print("-" * 50)
        display_df = df.drop(columns=["策略"], errors="ignore")
        print(display_df.to_string(index=False))
        total += len(df)

    print(f"\n{'='*60}")
    print(f"共 {total} 只候选股")
    print(f"  止损: -{config.risk.stop_loss_pct}%  止盈: +{config.risk.take_profit_pct}%")
    print(f"{'='*60}\n")
