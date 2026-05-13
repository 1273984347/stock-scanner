"""选股解读 —— LLM 驱动 + 模板回退双模式

有 API key → AI 深度解读
无 API key → 基于因子数据的模板化文本生成
"""
import os
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def _generate_template_analysis(code: str, name: str, factors: Dict,
                                 signals: Dict, fundamental_scores: Dict = None) -> str:
    """基于因子数据的模板化文本生成（无需 API key）

    Args:
        code: 股票代码
        name: 股票名称
        factors: 因子数据（rsi_14, momentum_5, volume_ratio 等）
        signals: 信号数据（trend, momentum, combined 等）
        fundamental_scores: 基本面评分

    Returns:
        结构化分析文本
    """
    lines = [f"【{name} ({code}) 选股分析】", ""]

    # 信号判断
    combined = signals.get("combined", {})
    signal = combined.get("signal", "neutral")
    confidence = combined.get("confidence", 0)
    signal_label = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}
    lines.append(f"综合信号: {signal_label.get(signal, signal)} (置信度 {confidence:.1%})")

    # 信号明细
    signal_details = signals.get("signals", {})
    if signal_details:
        lines.append("")
        lines.append("【各策略信号】")
        for sname, sresult in signal_details.items():
            sig = sresult.get("signal", "neutral")
            conf = sresult.get("confidence", 0)
            sl = signal_label.get(sig, sig)
            lines.append(f"  {sname}: {sl} ({conf:.1%})")

    # 关键因子
    if factors:
        lines.append("")
        lines.append("【关键因子】")
        rsi = factors.get("rsi_14")
        if rsi is not None:
            lines.append(f"  RSI(14): {rsi:.1f} - {'超买' if rsi > 70 else '超卖' if rsi < 30 else '正常区间'}")

        mom_5 = factors.get("momentum_5")
        if mom_5 is not None:
            lines.append(f"  动量(5日): {mom_5:+.2f}%")

        vol_20 = factors.get("volatility_20")
        if vol_20 is not None:
            lines.append(f"  波动率(20日): {vol_20:.1f}%")

        vol_ratio = factors.get("volume_ratio")
        if vol_ratio is not None:
            dir_label = "放量" if vol_ratio > 1.2 else "缩量" if vol_ratio < 0.8 else "正常"
            lines.append(f"  量比: {vol_ratio:.2f} ({dir_label})")

    # 市场体制
    regime = signals.get("regime", {})
    if regime:
        regime_map = {"TREND_UP": "趋势向上", "TREND_DOWN": "趋势向下",
                      "RANGE": "震荡", "RISK_OFF": "风险规避"}
        lines.append("")
        lines.append(f"  市场体制: {regime_map.get(regime.get('regime', ''), regime.get('regime', ''))}")
        lines.append(f"  体制评分: {regime.get('score', 0):.3f}")

    # 宏观门控
    macro = signals.get("macro_gate", {})
    if macro:
        state_map = {"ALLOW_FULL": "正常交易", "ALLOW_REDUCED": "减仓", "BLOCK_NEW_RISK": "禁止开仓"}
        lines.append(f"  宏观门控: {state_map.get(macro.get('state', ''), macro.get('state', ''))}")

    # 综合建议
    lines.append("")
    lines.append("【投资建议】")
    if signal == "bullish" and confidence > 0.3:
        lines.append(f"  [+] 建议关注 (信号确认)")
        lines.append(f"  止损线: -5% | 止盈线: +10% | 最大持仓: 5天")
    elif signal == "bearish" and confidence > 0.3:
        lines.append(f"  ✗ 建议回避 (空头信号)")
    else:
        lines.append(f"  ○ 建议观望 (信号不明确)")

    lines.append("")
    lines.append("[!] 以上分析基于量化因子数据，仅供参考，不构成投资建议。")

    return "\n".join(lines)


def _generate_llm_analysis(code: str, name: str, factors: Dict,
                            signals: Dict) -> str:
    """LLM 驱动的深度解读

    支持 DeepSeek / Kimi / MiMo 三种 API（按环境变量自动检测）
    设置对应环境变量即可使用：
    - DEEPSEEK_API_KEY → 深度求索
    - KIMI_API_KEY → 月之暗面 Kimi
    - MIMO_API_KEY → 小米 MiMo
    """
    # 按优先级检测 API key
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    provider = "deepseek"

    if not api_key:
        api_key = os.environ.get("KIMI_API_KEY", "")
        provider = "kimi"
    if not api_key:
        api_key = os.environ.get("MIMO_API_KEY", "")
        provider = "mimo"

    if not api_key:
        logger.info("无 LLM API key，回退到模板分析")
        return _generate_template_analysis(code, name, factors, signals)

    # 构建 prompt
    prompt = f"""分析以下 A 股股票并给出投资建议：
股票代码: {code}
股票名称: {name}

因子数据:
{_format_factors(factors)}

信号数据:
{_format_signals(signals)}

请从以下几个方面分析：
1. 技术面：趋势、动量、成交量
2. 估值面：当前估值水平
3. 风险点：需要注意什么
4. 操作建议：买入/持有/回避，及具体策略
"""

    try:
        if provider == "deepseek":
            return _call_deepseek(prompt, api_key)
        elif provider == "kimi":
            return _call_kimi(prompt, api_key)
        elif provider == "mimo":
            return _call_mimo(prompt, api_key)
    except Exception as e:
        logger.error(f"LLM 调用失败 ({provider}): {e}")
        # 尝试下一个 provider
        return _generate_template_analysis(code, name, factors, signals)


def _call_deepseek(prompt: str, api_key: str) -> str:
    """调用 DeepSeek API（兼容 OpenAI 格式）"""
    import requests
    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 500},
        timeout=30,
    )
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_kimi(prompt: str, api_key: str) -> str:
    """调用 Kimi API（兼容 OpenAI 格式）"""
    import requests
    resp = requests.post(
        "https://api.moonshot.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": "kimi-k2.6", "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 500},
        timeout=30,
    )
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_mimo(prompt: str, api_key: str) -> str:
    """调用 MiMo API（兼容 OpenAI 格式）"""
    import requests
    resp = requests.post(
        "https://api.xiaomimimo.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": "mimo-v2.5", "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.3, "max_tokens": 500},
        timeout=30,
    )
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _format_factors(factors: Dict) -> str:
    lines = []
    for k, v in factors.items():
        if isinstance(v, float):
            lines.append(f"  {k}: {v:.2f}")
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _format_signals(signals: Dict) -> str:
    import json
    return json.dumps(signals, ensure_ascii=False, indent=2, default=str)


def analyze_stock(code: str, name: str, factors: Dict,
                  signals: Dict, fundamental_scores: Dict = None,
                  use_llm: bool = True) -> str:
    """分析股票并生成解读

    有 LLM API key → LLM 深度解读
    无 LLM API key → 模板化文本生成

    Args:
        use_llm: 是否尝试用 LLM（设为 False 强制模板模式）
    """
    if use_llm:
        return _generate_llm_analysis(code, name, factors, signals)
    return _generate_template_analysis(code, name, factors, signals, fundamental_scores)
