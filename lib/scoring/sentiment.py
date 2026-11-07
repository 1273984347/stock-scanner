"""新闻情绪分析 —— NLP 驱动的市场情绪指标

数据源:
- akshare 新闻接口（东方财富/新浪财经）
- 本地关键词匹配 + 情感词典（无需 LLM API）
- 可选: LLM 深度分析（需配置 LLMConfig）

使用方式：
    from lib.scoring.sentiment import get_stock_sentiment, get_market_sentiment
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter
import logging
import re
import time

logger = logging.getLogger(__name__)

# 中文情感词典（精简版，覆盖金融场景）
POSITIVE_WORDS = {
    "利好", "上涨", "增长", "突破", "新高", "盈利", "超预期", "扩产", "回购",
    "增持", "涨停", "大涨", "反弹", "回暖", "景气", "龙头", "创新高", "放量",
    "主力", "净买入", "看多", "牛市", "强势", "拉升", "爆发", "加速", "翻倍",
    "减税", "降息", "降准", "刺激", "扶持", "补贴", "政策利好", "业绩预增",
    "高送转", "分红", "派息", "利润增长", "营收增长", "订单", "中标",
}

NEGATIVE_WORDS = {
    "利空", "下跌", "下滑", "暴跌", "跌停", "亏损", "不及预期", "减持", "质押",
    "违规", "处罚", "退市", "爆雷", "爆仓", "清仓", "看空", "熊市", "弱势",
    "缩量", "跳水", "闪崩", "崩盘", "恐慌", "踩踏", "资金出逃", "净卖出",
    "加息", "收紧", "制裁", "关税", "贸易战", "黑天鹅", "灰犀牛",
    "业绩预减", "商誉减值", "坏账", "诉讼", "仲裁", "立案调查",
    "暂停上市", "风险警示", "ST", "*ST",
}

# 行业特定情感词
SECTOR_SENTIMENT = {
    "半导体": {"利好": ["国产替代", "自主可控", "芯片"], "利空": ["制裁", "断供"]},
    "新能源": {"利好": ["碳中和", "新能源车", "光伏"], "利空": ["补贴退坡", "产能过剩"]},
    "医药": {"利好": ["集采中标", "创新药", "出海"], "利空": ["集采落标", "一致性评价"]},
    "银行": {"利好": ["净息差扩大", "不良率下降"], "利空": ["坏账", "拨备"]},
}

# 缓存
_SENTIMENT_CACHE: Dict[str, dict] = {}
_CACHE_TTL = 1800  # 30 分钟


def _safe_call(func, *args, max_retries=2, **kwargs):
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                logger.debug(f"情绪 API 调用失败: {e}")
                return None


def _score_text(text: str, sector: str = None) -> float:
    """对文本进行情感评分

    Returns:
        float: [-1, 1] 负数=看空，正数=看多
    """
    if not text:
        return 0.0

    text_lower = text.lower()
    pos_count = 0
    neg_count = 0

    for w in POSITIVE_WORDS:
        if w in text_lower:
            pos_count += 1

    for w in NEGATIVE_WORDS:
        if w in text_lower:
            neg_count += 1

    # 行业特定词
    if sector and sector in SECTOR_SENTIMENT:
        for w in SECTOR_SENTIMENT[sector].get("利好", []):
            if w in text_lower:
                pos_count += 0.5
        for w in SECTOR_SENTIMENT[sector].get("利空", []):
            if w in text_lower:
                neg_count += 0.5

    total = pos_count + neg_count
    if total == 0:
        return 0.0

    return (pos_count - neg_count) / total


def get_stock_news(code: str, days: int = 7) -> List[Dict]:
    """获取个股新闻

    Returns:
        list of dict: [{title, content, date, source, url}, ...]
    """
    try:
        import akshare as ak
        # 东方财富个股新闻
        df = _safe_call(ak.stock_news_em, symbol=code)
        if df is not None and not df.empty:
            news = []
            for _, row in df.iterrows():
                title = str(row.get("新闻标题", row.get("title", "")))
                content = str(row.get("新闻内容", row.get("content", "")))
                date = str(row.get("发布时间", row.get("date", "")))
                news.append({
                    "title": title,
                    "content": content[:500],  # 截断长文本
                    "date": date,
                    "source": str(row.get("文章来源", row.get("source", ""))),
                })
            return news[:50]  # 最多 50 条
    except Exception as e:
        logger.debug(f"获取 {code} 新闻失败: {e}")

    return []


def get_market_news(days: int = 3) -> List[Dict]:
    """获取市场整体新闻"""
    try:
        import akshare as ak
        # 财经新闻
        df = _safe_call(ak.stock_news_em, symbol="市场")
        if df is not None and not df.empty:
            return [
                {
                    "title": str(row.get("新闻标题", "")),
                    "content": str(row.get("新闻内容", ""))[:500],
                    "date": str(row.get("发布时间", "")),
                }
                for _, row in df.head(30).iterrows()
            ]
    except Exception as e:
        logger.debug(f"获取市场新闻失败: {e}")

    return []


def get_stock_sentiment(code: str, sector: str = None, days: int = 7) -> Dict:
    """计算个股情绪指标

    Args:
        code: 股票代码
        sector: 所属行业（可选，用于行业特定情感词）
        days: 回溯天数

    Returns:
        dict with keys:
        - sentiment_score: 综合情绪分 [-1, 1]
        - positive_ratio: 正面新闻占比
        - negative_ratio: 负面新闻占比
        - news_count: 新闻数量
        - hot_topics: 热点话题（出现最多的关键词）
        - sentiment_trend: 情绪趋势（最近3天 vs 之前）
    """
    cache_key = f"{code}_{days}"
    if cache_key in _SENTIMENT_CACHE:
        cached = _SENTIMENT_CACHE[cache_key]
        if time.time() - cached.get("_ts", 0) < _CACHE_TTL:
            return cached

    news = get_stock_news(code, days)
    if not news:
        return {
            "sentiment_score": 0.0,
            "positive_ratio": 0.0,
            "negative_ratio": 0.0,
            "news_count": 0,
            "hot_topics": [],
            "sentiment_trend": 0.0,
        }

    # 评分
    scores = []
    for item in news:
        text = item.get("title", "") + " " + item.get("content", "")
        score = _score_text(text, sector)
        scores.append(score)

    scores_arr = np.array(scores)
    pos_ratio = float(np.mean(scores_arr > 0.1))
    neg_ratio = float(np.mean(scores_arr < -0.1))
    avg_score = float(np.mean(scores_arr))

    # 热点话题
    all_text = " ".join(item.get("title", "") for item in news)
    # 提取 2-4 字关键词
    keywords = []
    for w in POSITIVE_WORDS | NEGATIVE_WORDS:
        if w in all_text:
            keywords.append(w)
    hot_topics = [w for w, _ in Counter(keywords).most_common(5)]

    # 情绪趋势
    mid = len(scores) // 2
    if mid > 0:
        recent = np.mean(scores_arr[mid:])
        earlier = np.mean(scores_arr[:mid])
        trend = recent - earlier
    else:
        trend = 0.0

    result = {
        "sentiment_score": round(avg_score, 4),
        "positive_ratio": round(pos_ratio, 4),
        "negative_ratio": round(neg_ratio, 4),
        "news_count": len(news),
        "hot_topics": hot_topics,
        "sentiment_trend": round(trend, 4),
    }

    _SENTIMENT_CACHE[cache_key] = {**result, "_ts": time.time()}
    return result


def get_market_sentiment(days: int = 3) -> Dict:
    """计算市场整体情绪

    Returns:
        dict with keys:
        - market_sentiment: 市场情绪分 [-1, 1]
        - fear_greed: 恐慌贪婪指数 [0, 100]（50=中性）
        - news_volume: 新闻量（与历史均值比较）
        - sector_sentiment: 各行业情绪 {sector: score}
    """
    news = get_market_news(days)
    if not news:
        return {
            "market_sentiment": 0.0,
            "fear_greed": 50,
            "news_volume": 1.0,
            "sector_sentiment": {},
        }

    # 整体评分
    scores = [_score_text(item.get("title", "") + " " + item.get("content", "")) for item in news]
    avg = float(np.mean(scores)) if scores else 0

    # 恐慌贪婪指数（映射到 0-100）
    fear_greed = int(min(100, max(0, (avg + 1) * 50)))

    # 行业情绪
    sector_scores = {}
    for sector in SECTOR_SENTIMENT:
        sector_news = [n for n in news if sector in n.get("title", "") or sector in n.get("content", "")]
        if sector_news:
            sector_s = np.mean([_score_text(n.get("title", ""), sector) for n in sector_news])
            sector_scores[sector] = round(float(sector_s), 3)

    return {
        "market_sentiment": round(avg, 4),
        "fear_greed": fear_greed,
        "news_volume": len(news) / 20.0,  # 标准化
        "sector_sentiment": sector_scores,
    }


def get_sentiment_factor(code: str, sector: str = None) -> float:
    """获取情绪因子值（供 factors.py 使用）

    Returns:
        float: [-100, 100] 范围的情绪因子值，正=看多
    """
    result = get_stock_sentiment(code, sector)
    score = result["sentiment_score"]
    trend = result["sentiment_trend"]

    # 基础分 + 趋势加成
    factor = score * 80 + trend * 20
    return max(-100, min(100, factor))


def clear_cache():
    """清除缓存"""
    _SENTIMENT_CACHE.clear()
