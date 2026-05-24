"""tests/test_memory_log.py - TradingMemoryLog 测试"""
import pytest
import tempfile
import os
from pathlib import Path

from lib.llm_utils.memory import TradingMemoryLog
from lib.llm_utils.rating import parse_rating, RATINGS_5_TIER


class TestParseRating:
    """parse_rating 测试"""

    def test_explicit_rating_label(self):
        """显式 Rating: X 标签"""
        assert parse_rating("Rating: Buy") == "Buy"
        assert parse_rating("rating: Overweight") == "Overweight"
        assert parse_rating("Rating - Hold") == "Hold"

    def test_markdown_bold(self):
        """Markdown 粗体格式"""
        assert parse_rating("Rating: **Buy**") == "Buy"
        assert parse_rating("Rating: *Sell*") == "Sell"

    def test_fallback_first_word(self):
        """无显式标签时回退到第一个评分词"""
        assert parse_rating("I think this is a Buy opportunity") == "Buy"
        assert parse_rating("Underweight rating for this") == "Underweight"

    def test_default_hold(self):
        """未找到评分时返回默认 Hold"""
        assert parse_rating("No rating here") == "Hold"
        assert parse_rating("") == "Hold"

    def test_case_insensitive(self):
        """大小写不敏感"""
        assert parse_rating("RATING: BUY") == "Buy"
        assert parse_rating("buy") == "Buy"


class TestTradingMemoryLog:
    """TradingMemoryLog 测试"""

    def test_store_decision_and_load(self, tmp_path):
        """存储决策并读取"""
        log_path = tmp_path / "test_log.md"
        log = TradingMemoryLog({"memory_log_path": str(log_path)})

        log.store_decision("000001", "2024-01-15", "Rating: Buy\nA股银行股值得买入")

        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["ticker"] == "000001"
        assert entries[0]["rating"] == "Buy"
        assert entries[0]["pending"] is True
        assert "A股银行股值得买入" in entries[0]["decision"]

    def test_idempotency(self, tmp_path):
        """幂等性：重复 store_decision 不创建重复条目"""
        log_path = tmp_path / "test_log.md"
        log = TradingMemoryLog({"memory_log_path": str(log_path)})

        log.store_decision("000001", "2024-01-15", "Rating: Buy")
        log.store_decision("000001", "2024-01-15", "Rating: Buy")  # 重复

        entries = log.load_entries()
        assert len(entries) == 1  # 只有一个

    def test_update_with_outcome(self, tmp_path):
        """Phase B 更新结果"""
        log_path = tmp_path / "test_log.md"
        log = TradingMemoryLog({"memory_log_path": str(log_path)})

        log.store_decision("000001", "2024-01-15", "Rating: Buy")
        log.update_with_outcome(
            ticker="000001",
            trade_date="2024-01-15",
            raw_return=0.12,
            alpha_return=0.05,
            holding_days=5,
            reflection="低估了银行股波动率",
        )

        entries = log.load_entries()
        assert len(entries) == 1
        assert entries[0]["pending"] is False
        assert entries[0]["raw"] == "+12.0%"
        assert entries[0]["alpha"] == "+5.0%"
        assert entries[0]["holding"] == "5d"
        assert "低估了银行股波动率" in entries[0]["reflection"]

    def test_get_past_context_same_ticker(self, tmp_path):
        """同 ticker 历史上下文"""
        log_path = tmp_path / "test_log.md"
        log = TradingMemoryLog({"memory_log_path": str(log_path)})

        log.store_decision("000001", "2024-01-10", "Rating: Buy")
        log.update_with_outcome("000001", "2024-01-10", 0.10, 0.03, 3, "第一次成功")
        log.store_decision("000001", "2024-01-15", "Rating: Overweight")
        log.update_with_outcome("000001", "2024-01-15", -0.05, -0.02, 2, "回调止损")

        context = log.get_past_context("000001", n_same=5)
        assert "000001" in context
        assert "Buy" in context
        assert "Overweight" in context
        assert "历史 000001 分析" in context

    def test_get_past_context_cross_ticker(self, tmp_path):
        """跨 ticker 教训"""
        log_path = tmp_path / "test_log.md"
        log = TradingMemoryLog({"memory_log_path": str(log_path)})

        log.store_decision("000001", "2024-01-10", "Rating: Buy")
        log.update_with_outcome("000001", "2024-01-10", 0.10, 0.03, 3, "银行板块联动效应显著")
        log.store_decision("600000", "2024-01-15", "Rating: Sell")

        context = log.get_past_context("600000", n_same=5, n_cross=3)
        assert "跨股票教训" in context
        assert "000001" in context

    def test_rotation_max_entries(self, tmp_path):
        """超过 max_entries 时自动删除最旧条目"""
        log_path = tmp_path / "test_log.md"
        log = TradingMemoryLog({
            "memory_log_path": str(log_path),
            "memory_log_max_entries": 3,
        })

        for i in range(5):
            log.store_decision(f"00{i:04d}", f"2024-01-{i+10:02d}", "Rating: Buy")
            log.update_with_outcome(
                f"00{i:04d}", f"2024-01-{i+10:02d}",
                0.05, 0.01, 2, f"反思 {i}"
            )

        entries = log.load_entries()
        assert len(entries) == 3  # 最多保留 3 个

    def test_batch_update(self, tmp_path):
        """批量更新"""
        log_path = tmp_path / "test_log.md"
        log = TradingMemoryLog({"memory_log_path": str(log_path)})

        log.store_decision("000001", "2024-01-10", "Rating: Buy")
        log.store_decision("000002", "2024-01-11", "Rating: Overweight")

        log.batch_update_with_outcomes([
            {"ticker": "000001", "trade_date": "2024-01-10",
             "raw_return": 0.10, "alpha_return": 0.03, "holding_days": 5,
             "reflection": "收益达标"},
            {"ticker": "000002", "trade_date": "2024-01-11",
             "raw_return": -0.03, "alpha_return": -0.01, "holding_days": 2,
             "reflection": "波动超预期"},
        ])

        entries = log.load_entries()
        assert len(entries) == 2
        ratings = {e["ticker"]: e["raw"] for e in entries}
        assert ratings["000001"] == "+10.0%"
        assert ratings["000002"] == "-3.0%"

    def test_no_path_no_op(self):
        """无 log_path 时所有操作静默忽略"""
        log = TradingMemoryLog({})
        log.store_decision("000001", "2024-01-15", "Rating: Buy")  # 不抛异常
        log.update_with_outcome("000001", "2024-01-15", 0.1, 0.05, 3, "反思")  # 不抛异常
        assert log.load_entries() == []
