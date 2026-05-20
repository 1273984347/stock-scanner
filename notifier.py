"""飞书推送模块"""

import requests
import json
import pandas as pd
from datetime import datetime
from config import (FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_RECEIVE_ID,
                    FEISHU_RECEIVE_ID_TYPE, get_config)
from factors import detect_market_regime, evaluate_macro_gate
from scanner import get_realtime_quotes, get_market_sentiment
import logging

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


def send_feishu_message(results: dict, preset: str = "balanced") -> bool:
    if not results:
        logger.info("无结果，跳过推送")
        return True
    if not FEISHU_RECEIVE_ID:
        logger.error("FEISHU_RECEIVE_ID 未配置")
        return False
    token = get_tenant_token()
    if not token:
        return False
    content = build_message_content(results, preset)
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
            logger.info("飞书推送成功")
            return True
        logger.error(f"飞书返回错误: {data}")
        return False
    except Exception as e:
        logger.error(f"飞书推送异常: {e}")
        return False


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
