"""Chart builder for stock scanner reports.

Handles matplotlib-based chart generation with memory-safe practices.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# Lazy import matplotlib — not all environments have it installed
matplotlib = None
plt = None

def _ensure_matplotlib():
    """Lazy-import matplotlib, return True if available."""
    global matplotlib, plt
    if matplotlib is not None:
        return True
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        return True
    except ImportError:
        logger.warning("matplotlib not installed, chart generation disabled")
        return False


# --- Public API ---


def cleanup_old_charts(days_keep: int = 7) -> int:
    """Delete .png files in cache/reports/ older than days_keep days.

    Returns:
        Number of files removed.
    """
    reports_dir = Path("cache/reports")
    if not reports_dir.is_dir():
        return 0

    cutoff = datetime.now() - timedelta(days=days_keep)
    removed = 0

    for f in reports_dir.glob("*.png"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
                removed += 1
        except OSError as e:
            logger.debug("Could not remove %s: %s", f, e)

    if removed:
        logger.info("Removed %d old chart file(s)", removed)
    return removed


def build_all_charts(results: dict) -> List[str]:
    """Build all charts from results dict.

    Args:
        results: {strategy_name: DataFrame}

    Returns:
        List of PNG file paths (str). Empty list on error.
    """
    if not _ensure_matplotlib():
        return []

    try:
        _setup_font()
        _dark_style()
    except Exception as e:
        logger.warning("Failed to configure matplotlib style: %s", e)
        return []

    reports_dir = Path("cache/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    paths: List[str] = []

    try:
        p = _build_distribution_pie(results, reports_dir / "distribution.png")
        if p:
            paths.append(p)
    except Exception as e:
        logger.warning("distribution pie chart failed: %s", e)

    try:
        p = _build_sentiment_gauge(results, reports_dir / "sentiment.png")
        if p:
            paths.append(p)
    except Exception as e:
        logger.warning("sentiment gauge chart failed: %s", e)

    return paths


# --- Internal setup ---


def _setup_font() -> bool:
    """Configure sans-serif font for matplotlib.

    Returns:
        True if a CJK-capable font was configured, False otherwise.
    """
    from matplotlib.font_manager import FontProperties

    # Known CJK font names matplotlib can find
    font_names = [
        'Microsoft YaHei',
        'Microsoft YaHei UI',
        'SimHei',
        'Noto Sans CJK SC',
        'WenQuanYi Micro Hei',
        'Arial Unicode MS',
    ]

    for name in font_names:
        try:
            # FontProperties with a known font name — if matplotlib finds it, no exception
            fp = FontProperties(family=name)
            # Verify by trying to get a sample string's width (crude but works)
            fig_test = plt.figure()
            ax_test = fig_test.add_subplot(111)
            try:
                ax_test.text(0.5, 0.5, '测试', fontproperties=fp)
                fig_test.canvas.draw()
                plt.close(fig_test)
                plt.rcParams['font.sans-serif'] = [name]
                plt.rcParams['axes.unicode_minus'] = False
                return True
            except Exception:
                plt.close(fig_test)
                continue
        except Exception:
            pass

    # Last resort: ASCII fallback
    plt.rcParams['axes.unicode_minus'] = False
    logger.warning("No CJK font found, falling back to ASCII labels")
    return False


def _dark_style():
    """Apply dark background style to matplotlib."""
    plt.style.use('dark_background')
    plt.rcParams['figure.facecolor'] = '#1a1a2e'
    plt.rcParams['axes.facecolor'] = '#1a1a2e'
    plt.rcParams['grid.color'] = '#333'


# --- Chart builders ---


def _build_distribution_pie(results: dict, save_path: Path) -> Optional[str]:
    """Build a pie chart of strategy distribution.

    Returns:
        Path string, or None if nothing to chart.
    """
    # Count non-empty DataFrames
    non_empty = sum(
        1 for v in results.values()
        if hasattr(v, 'empty') and not v.empty
    )
    if non_empty == 0:
        return None

    fig, ax = plt.subplots(figsize=(8, 4))
    labels = list(results.keys())[:6]
    sizes = [
        0 if getattr(results.get(k), 'empty', True) else 1
        for k in labels
    ]
    # Collapse zero entries
    pairs = [(l, s) for l, s in zip(labels, sizes) if s > 0]
    if not pairs:
        plt.close(fig)
        return None
    labels, sizes = zip(*pairs)

    colors = ['#4ecdc4', '#ff6b6b', '#ffe66d', '#95e1d3', '#f38181', '#aa96da']
    ax.pie(sizes, labels=labels, colors=colors[:len(labels)], autopct='%1.0f%%')
    ax.set_title('候选股策略分布')
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)
    return str(save_path)


def _build_sentiment_gauge(results: dict, save_path: Path) -> Optional[str]:
    """Build a sentiment gauge bar chart.

    Returns:
        Path string, or None on error.
    """
    try:
        from scanner import get_market_sentiment
        sentiment = get_market_sentiment()
    except Exception as e:
        logger.debug("Could not get market sentiment: %s", e)
        sentiment = 0.5

    fig, ax = plt.subplots(figsize=(8, 3))

    if sentiment > 0.6:
        color = '#4ecdc4'
    elif sentiment > 0.4:
        color = '#ffe66d'
    else:
        color = '#ff6b6b'

    ax.barh(['市场情绪'], [sentiment], color=color)
    ax.set_xlim(0, 1)
    ax.axvline(x=0.5, color='white', linestyle='--', linewidth=1)
    ax.set_title('市场情绪指数')
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)
    return str(save_path)


def build_position_returns_chart(save_path: Path) -> Optional[str]:
    """生成持仓收益柱状图（持仓 vs 沪深300基准对比）.

    - 从 PositionManager 读取当前持仓
    - 获取沪深300同期涨幅作为基准
    - 绘制水平柱状图：每个持仓收益 vs 基准

    Returns:
        PNG 路径，或 None（无持仓/获取失败）
    """
    if not _ensure_matplotlib():
        return None

    try:
        from lib.risk.position_manager import PositionManager
        from scanner import ScanOrchestrator
    except ImportError:
        return None

    try:
        orchestrator = ScanOrchestrator()
        quotes = orchestrator.get_realtime_quotes()
        prices = {}
        if not quotes.empty:
            for _, row in quotes.iterrows():
                code = str(row.get("代码", ""))
                price = row.get("最新价")
                if code and price:
                    prices[code] = float(price)
    except Exception as e:
        logger.debug("获取实时行情失败: %s", e)
        prices = {}

    try:
        pm = PositionManager()
        summary = pm.get_portfolio_summary(prices)
    except Exception as e:
        logger.debug("PositionManager 初始化失败: %s", e)
        return None

    details = summary.get("details", [])
    if not details:
        return None

    # 获取沪深300基准收益率
    benchmark_return = 0.0
    try:
        import numpy as np
        hs300_path = Path("cache/hs300_cache.parquet")
        if hs300_path.exists():
            import pandas as pd
            df = pd.read_parquet(hs300_path)
            if "close" in df.columns and len(df) >= 2:
                benchmark_return = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
    except Exception:
        pass

    # 提取持仓代码和收益率
    codes = [d["code"] for d in details[:10]]  # 最多10只
    pnl_pcts = [d["pnl_pct"] * 100 for d in details[:10]]  # 转为百分比

    if not codes:
        return None

    fig, ax = plt.subplots(figsize=(8, max(2, len(codes) * 0.6)))

    colors = []
    for pct in pnl_pcts:
        if pct > 0:
            colors.append('#4ecdc4')
        elif pct < -3:
            colors.append('#ff6b6b')
        else:
            colors.append('#ffe66d')

    y_pos = range(len(codes))
    bars = ax.barh(y_pos, pnl_pcts, color=colors, height=0.5)

    # 基准线
    ax.axvline(x=benchmark_return, color='#888', linestyle='--', linewidth=1.5,
                label=f'沪深300 {benchmark_return:+.1f}%')

    ax.set_yticks(y_pos)
    ax.set_yticklabels(codes)
    ax.set_xlabel('收益率 (%)')
    ax.set_title('持仓收益 vs 沪深300基准')
    ax.legend(loc='lower right')
    ax.grid(axis='x', color='#333', linewidth=0.5)

    # 标注数值
    for bar, pct in zip(bars, pnl_pcts):
        ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                 f'{pct:+.1f}%', va='center', fontsize=9, color='#eee')

    fig.tight_layout()
    fig.savefig(save_path, dpi=100, bbox_inches='tight')
    plt.close(fig)
    return str(save_path)
