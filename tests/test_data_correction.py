"""数据修正器测试"""
import pytest
from lib.data_correction import DataCorrection


class TestDataCorrection:
    def _make(self, tmp_path):
        return DataCorrection()

    def test_detect_price_jump(self, tmp_path):
        """检测 >20% 价格跳变"""
        dc = self._make(tmp_path)
        data = [
            {"date": "2024-01-01", "close": 100, "volume": 1000},
            {"date": "2024-01-02", "close": 130, "volume": 1000},  # +30% 跳变
        ]
        anomalies = dc.detect_anomalies(data)
        assert any(a["type"] == "price_jump" for a in anomalies)

    def test_no_correction_for_limit_up(self, tmp_path):
        """真实涨停（10%）不应被修正"""
        dc = self._make(tmp_path)
        data = [
            {"date": "2024-01-01", "close": 100, "open": 100, "high": 100, "low": 100, "volume": 1000},
            {"date": "2024-01-02", "close": 110, "open": 110, "high": 110, "low": 110, "volume": 1000},  # 10% 涨停
            {"date": "2024-01-03", "close": 110, "open": 110, "high": 110, "low": 110, "volume": 1000},  # 持续
        ]
        anomalies = dc.detect_anomalies(data)
        # 10% < 20% threshold → 不应被检测
        assert not any(a["type"] == "price_jump" for a in anomalies)

    def test_p1_10_correction_uses_prev_only(self, tmp_path):
        """P1-10: 修正值应只用 prev 数据（不用 next_close 前视）

        设计：
        - prev_close = 100（前一日）
        - curr_close = 200（异常暴涨）
        - next_close = 100（后一日）
        - 旧版：(prev + next) / 2 = 100
        - 新版：前 N 日均值（不含 200 那天）= 100
        - 测试关键：构造 prev != next 的场景，验证修正值不依赖 next
        """
        dc = self._make(tmp_path)
        # 场景：prev 持续 150，curr 暴涨 300（异常），next 回归 100
        data = [
            {"date": "2024-01-01", "close": 150, "open": 150, "high": 150, "low": 150, "volume": 1000},
            {"date": "2024-01-02", "close": 150, "open": 150, "high": 150, "low": 150, "volume": 1000},
            {"date": "2024-01-03", "close": 150, "open": 150, "high": 150, "low": 150, "volume": 1000},
            {"date": "2024-01-04", "close": 300, "open": 300, "high": 300, "low": 300, "volume": 1000},  # 异常 +100%
            {"date": "2024-01-05", "close": 100, "open": 100, "high": 100, "low": 100, "volume": 1000},
        ]
        anomalies = dc.detect_anomalies(data)
        assert any(a["type"] == "price_jump" for a in anomalies)

        corrected = dc.correct_data(data, anomalies)
        jump_anomaly = next(a for a in anomalies if a["type"] == "price_jump")
        idx = jump_anomaly["index"]
        corrected_value = corrected[idx]["close"]

        # 关键：旧版 (prev + next) / 2 = (150 + 100) / 2 = 125
        # 新版 前 3 日均值 = 150
        # 验证：修正值 ≠ 125（前视标志）
        assert corrected_value != 125, (
            f"P1-10：修正值依赖了 next_close（前视）！"
            f"旧逻辑得 125，新逻辑应 ≈ 150（前 3 日均值）"
        )
        # 新逻辑应 ≈ 150（前 3 日均值）
        assert abs(corrected_value - 150) < 1, (
            f"修正值应 ≈ 前 3 日均值 150，实际 {corrected_value}"
        )
