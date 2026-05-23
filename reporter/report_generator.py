"""文字报告生成器"""

import json
import logging
from datetime import date, datetime
from glob import glob
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 依赖模块（可选，测试环境可能不可用）
# ---------------------------------------------------------------------------
try:
    from config import get_config
except ImportError:
    get_config = None  # type: ignore

try:
    from scanner import get_market_sentiment
except ImportError:
    get_market_sentiment = None  # type: ignore

try:
    from factors import evaluate_macro_gate
except ImportError:
    evaluate_macro_gate = None  # type: ignore

# ---------------------------------------------------------------------------
# 1. _get_market_overview
# ---------------------------------------------------------------------------


def _get_market_overview() -> Tuple[float, str, float]:
    """获取市场情绪和宏观门控，返回 (sentiment, macro_state, position_multiplier)。"""
    sentiment = 0.5
    macro_state = "unknown"
    position_multiplier = 1.0
    try:
        if get_market_sentiment is not None:
            sentiment = get_market_sentiment()
        if evaluate_macro_gate is not None and get_market_sentiment is not None:
            macro_gate = evaluate_macro_gate(sentiment)
            macro_state = getattr(macro_gate, "state", "unknown")
            position_multiplier = getattr(macro_gate, "position_multiplier", 1.0)
    except Exception as e:
        logger.warning(f"[report_generator] 市场概览获取失败: {e}")
    return sentiment, macro_state, position_multiplier


# ---------------------------------------------------------------------------
# 2. is_trading_day
# ---------------------------------------------------------------------------


def is_trading_day() -> bool:
    """判断今天是否为 A 股交易日（仅检查周末，不接 holidays 包）"""
    today = date.today()
    return today.weekday() < 5  # 0=Mon … 4=Fri


# ---------------------------------------------------------------------------
# 2. _load_latest_results
# ---------------------------------------------------------------------------


def _load_latest_results() -> dict | None:
    """读取 cache/reports/strategy_*.json 中日期最新的一个文件，返回 dict。"""
    reports_dir = Path("cache/reports")
    if not reports_dir.exists():
        logger.warning("[report_generator] cache/reports/ 目录不存在")
        return None

    pattern = str(reports_dir / "strategy_*.json")
    files = sorted(glob(pattern), reverse=True)  # 最新的排前面
    if not files:
        logger.info("[report_generator] 未找到历史策略报告")
        return None

    latest = files[0]
    try:
        with open(latest, encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"[report_generator] 加载最新报告: {latest}")
        return data
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.error(f"[report_generator] 读取报告失败 {latest}: {e}")
        return None


# ---------------------------------------------------------------------------
# 3. _save_strategy_results
# ---------------------------------------------------------------------------


def _save_strategy_results(
    results: dict,
    sentiment: float,
    strategy_counts: dict,
) -> str:
    """保存策略结果到 cache/reports/strategy_{date}.json，返回保存路径。"""
    today_str = date.today().isoformat()
    save_path = Path(f"cache/reports/strategy_{today_str}.json")
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # 从 results 中提取信号统计（zt_count 等）
    signals: dict = {}
    for name, df in results.items():
        if hasattr(df, "empty") and df.empty:
            continue
        # 简单统计：总候选股数
        if name not in strategy_counts:
            try:
                signals[name] = len(df)
            except (AttributeError, TypeError):
                pass

    payload = {
        "date": today_str,
        "sentiment": sentiment,
        "signals": signals,
        "saved_at": datetime.now().isoformat(),
    }

    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info(f"[report_generator] 报告已保存: {save_path}")
    except (IOError, OSError) as e:
        logger.error(f"[report_generator] 保存报告失败: {e}")

    return str(save_path)


# ---------------------------------------------------------------------------
# 4. build_strategy_report
# ---------------------------------------------------------------------------


def build_strategy_report(results: dict, preset: str = "balanced") -> str:
    """生成策略执行后的文字报告（盘后版）。

    - 市场情绪 + 宏观门控
    - 各策略候选股数量统计
    - 风控规则
    - 保存结果到 cache/reports/
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"盘后选股信号 ({now_str})",
        f"策略: {preset}",
        "=" * 30,
        "",
    ]

    # ---- 市场情绪 ----
    sentiment_val, macro_gate_state, position_multiplier = _get_market_overview()
    macro_reasoning = ""

    if evaluate_macro_gate is not None and get_market_sentiment is not None:
        try:
            macro_gate = evaluate_macro_gate(sentiment_val)
            macro_reasoning = getattr(macro_gate, "reasoning", "")
        except Exception as e:
            logger.warning(f"[report_generator] evaluate_macro_gate 失败: {e}")

    lines.append("【市场概况】")
    lines.append(f"  情绪分数: {sentiment_val:.2f}")
    lines.append(f"  宏观门控: {macro_gate_state}")
    lines.append(f"  仓位乘数: {position_multiplier:.0%}")
    if macro_reasoning:
        lines.append(f"  说明: {macro_reasoning}")

    lines.append("")
    lines.append("=" * 30)
    lines.append("【候选股统计】")

    # ---- 候选股统计 ----
    strategy_counts: dict[str, int] = {}
    total = 0
    for name, df in results.items():
        try:
            is_empty = hasattr(df, "empty") and df.empty
        except (AttributeError, TypeError):
            is_empty = False
        if not is_empty:
            try:
                cnt = len(df)
            except (AttributeError, TypeError):
                cnt = 0
            strategy_counts[name] = cnt
            total += cnt

    for name, cnt in strategy_counts.items():
        lines.append(f"  {name}: {cnt} 只")

    if total == 0:
        lines.append("  今日无符合条件的候选股")
    else:
        lines.append(f"  共 {total} 只候选股")

    lines.append("")
    lines.append("=" * 30)
    lines.append("风控规则：")

    # ---- 风控规则 ----
    stop_loss_pct = 5.0
    take_profit_pct = 10.0
    trailing_stop = True
    trailing_pct = 3.0
    max_hold_days = 5

    if get_config is not None:
        try:
            cfg = get_config(preset)
            risk = cfg.risk
            stop_loss_pct = risk.stop_loss_pct
            take_profit_pct = risk.take_profit_pct
            trailing_stop = risk.trailing_stop
            trailing_pct = risk.trailing_pct
            max_hold_days = risk.max_hold_days
        except Exception as e:
            logger.warning(f"[report_generator] get_config 失败: {e}，使用默认风控参数")

    lines.append(f"  止损: -{stop_loss_pct}%")
    lines.append(f"  止盈: +{take_profit_pct}%")
    if trailing_stop:
        lines.append(f"  移动止盈: 回撤 {trailing_pct}%")
    lines.append(f"  最大持仓: {max_hold_days} 天")

    lines.append("")
    lines.append("仅供参考，不构成投资建议。")

    # ---- 保存 ----
    _save_strategy_results(results, sentiment_val, strategy_counts)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. build_morning_report
# ---------------------------------------------------------------------------


def build_morning_report() -> Tuple[str, List[str]]:
    """生成晨间文字报告。

    - 读取最近一次策略结果
    - 市场展望 + 昨日信号回顾
    - IC 监控状态（简化版）
    - 无持仓数据时只发信号/市场展望（简化版）

    Returns:
        (文字报告, 空列表)
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    latest = _load_latest_results()

    lines = [
        f"晨间市场展望 ({now_str})",
        "=" * 30,
        "",
    ]

    # ---- 市场情绪 ----
    sentiment_val, macro_gate_state, position_multiplier = _get_market_overview()
    macro_reasoning = ""

    if evaluate_macro_gate is not None and get_market_sentiment is not None:
        try:
            macro_gate = evaluate_macro_gate(sentiment_val)
            macro_reasoning = getattr(macro_gate, "reasoning", "")
        except Exception as e:
            logger.warning(f"[report_generator] evaluate_macro_gate 失败: {e}")

    lines.append("【市场展望】")
    lines.append(f"  情绪分数: {sentiment_val:.2f}")
    lines.append(f"  宏观门控: {macro_gate_state}")
    lines.append(f"  仓位乘数: {position_multiplier:.0%}")
    if macro_reasoning:
        lines.append(f"  说明: {macro_reasoning}")

    lines.append("")
    lines.append("=" * 30)

    # ---- 昨日信号回顾 ----
    if latest is not None:
        saved_date = latest.get("date", "未知日期")
        saved_sentiment = latest.get("sentiment", 0.0)
        saved_signals = latest.get("signals", {})
        lines.append(f"【昨日信号回顾】({saved_date})")
        lines.append(f"  昨日情绪: {saved_sentiment:.2f}")
        if saved_signals:
            total_sig = sum(v for v in saved_signals.values() if isinstance(v, int))
            for name, cnt in saved_signals.items():
                lines.append(f"  {name}: {cnt} 只")
            lines.append(f"  共 {total_sig} 只候选股")
        else:
            lines.append("  无昨日信号数据")
    else:
        lines.append("【昨日信号回顾】")
        lines.append("  未找到历史策略报告")

    lines.append("")
    lines.append("=" * 30)
    lines.append("【IC 监控状态】")
    lines.append("  数据源: TDX（通达信）")
    lines.append("  状态: 自动监控")

    lines.append("")
    lines.append("仅供参考，不构成投资建议。")

    return "\n".join(lines), []
