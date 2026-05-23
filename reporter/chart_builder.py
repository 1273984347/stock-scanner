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
    fonts = [
        'Noto Sans CJK SC',
        'Microsoft YaHei UI',
        'SimHei',
        'WenQuanYi Micro Hei',
        'Arial Unicode MS',
    ]
    for font in fonts:
        if font in matplotlib.font_manager.findSystemFonts():
            plt.rcParams['font.sans-serif'] = [font]
            plt.rcParams['axes.unicode_minus'] = False
            return True

    # Fallback: still try to set something; ASCII will work
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
        from reporter.scanner import StockScanner
        scanner = StockScanner()
        sentiment = scanner.get_market_sentiment()
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
