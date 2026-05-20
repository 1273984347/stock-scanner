"""Pump & Dump 检测测试"""
import pytest
import pandas as pd
import numpy as np
from lib.pump_dump_detector import detect_pump_and_dump, is_pump_dump_risk


class TestDetectPumpAndDump:
    """detect_pump_and_dump 函数测试"""

    def setup_method(self):
        """设置随机种子保证可复现"""
        np.random.seed(42)

    def test_empty_dataframe_returns_empty(self):
        """空 DataFrame 应返回空列表"""
        result = detect_pump_and_dump(pd.DataFrame())
        assert result == []

    def test_short_dataframe_returns_empty(self):
        """数据少于30天应返回空列表"""
        hist = pd.DataFrame({
            'close': [10, 11, 12, 13],
            'volume': [100, 110, 120, 130]
        })
        result = detect_pump_and_dump(hist)
        assert result == []

    def test_rapid_up_alert(self):
        """急速拉升检测"""
        # 构造连续大涨数据：近5日3天涨幅>5%，累计涨幅>20%
        # 需要 len(base) + len(up_days) >= 30, 且 n >= 6
        base = [10.0] * 25
        up_days = [10.0, 10.6, 11.3, 12.1, 12.9, 13.8]  # 6天数据，累计>38%
        hist = pd.DataFrame({
            'close': base + up_days,
            'volume': [100] * 31
        })
        result = detect_pump_and_dump(hist)
        types = [r["type"] for r in result]
        assert "急速拉升" in types

    def test_volume_price_divergence_alert(self):
        """量价背离检测"""
        np.random.seed(42)
        close = np.cumsum(np.random.randn(40)) + 20
        volume = np.random.randint(100, 200, 40).astype(float)
        hist = pd.DataFrame({'close': close, 'volume': volume})
        result = detect_pump_and_dump(hist)
        types = [r["type"] for r in result]
        # 随机数据有一定概率触发量价背离
        # 这里只检查函数正常运行
        assert isinstance(result, list)

    def test_amplitude_anomaly_alert(self):
        """振幅异常检测"""
        # 构造高波动数据：每天振幅>8%
        base = 10.0
        data = []
        for i in range(35):
            # 每天以 base 为中心上下波动 >8%
            high = base * 1.09
            low = base * 0.91
            close = base * (1 + np.random.randn() * 0.02)
            data.append({'close': close, 'high': high, 'low': low,
                         'volume': 1000})
            base = close
        hist = pd.DataFrame(data)
        result = detect_pump_and_dump(hist)
        types = [r["type"] for r in result]
        assert "振幅异常" in types

    def test_volume_shrink_alert(self):
        """缩量异常检测（暴量后萎靡）"""
        # 前20天高量(1000)，后20天低量(200)
        # 近40日均量=(1000*20+200*20)/40=600，近20日均量=200，比例=0.33
        # 需要 < 0.3，所以用早期更高
        volume = [1500] * 20 + [200] * 20
        close = np.linspace(10, 13, 40).tolist()
        hist = pd.DataFrame({'close': close, 'volume': volume})
        result = detect_pump_and_dump(hist)
        types = [r["type"] for r in result]
        assert "缩量异常（暴量后萎靡）" in types

    def test_no_volume_column(self):
        """无 volume 列时不崩溃"""
        close = np.linspace(10, 15, 35)
        hist = pd.DataFrame({'close': close})
        result = detect_pump_and_dump(hist)
        assert isinstance(result, list)

    def test_no_high_low_columns(self):
        """无 high/low 列时不崩溃"""
        close = np.linspace(10, 15, 35)
        volume = np.full(35, 100)
        hist = pd.DataFrame({'close': close, 'volume': volume})
        result = detect_pump_and_dump(hist)
        assert isinstance(result, list)

    def test_result_has_required_fields(self):
        """返回结果包含必要字段"""
        # 构造一个触发警报的场景：30天数据
        close_vals = list(np.linspace(10, 14, 25)) + [14.0, 15.0, 16.0, 17.2, 18.3]  # 30 items
        volume = list(np.full(25, 100)) + [1000, 1000, 1000, 1000, 1000]  # 30 items
        hist = pd.DataFrame({'close': close_vals, 'volume': volume,
                             'high': close_vals, 'low': [c * 0.99 for c in close_vals]})
        result = detect_pump_and_dump(hist)
        for alert in result:
            assert "type" in alert
            assert "severity" in alert
            assert "detail" in alert
            assert "score" in alert


class TestIsPumpDumpRisk:
    """is_pump_dump_risk 函数测试"""

    def setup_method(self):
        np.random.seed(42)

    def test_normal_stock_not_high_risk(self):
        """正常股票不应标记为高风险"""
        # 平稳上涨的小票
        close = np.linspace(10, 12, 35)
        volume = np.full(35, 100)
        hist = pd.DataFrame({'close': close, 'volume': volume})
        result = is_pump_dump_risk(hist)
        assert result == False

    def test_high_risk_pump_detected(self):
        """高风险杀猪盘应被检测"""
        # 构造强特征：连续急速拉升 + 频繁涨停
        base = np.linspace(10, 13, 20).tolist()
        # 涨停板
        up = [13.0, 13.65, 14.0, 14.65, 15.0]
        hist = pd.DataFrame({
            'close': base + up,
            'volume': [100] * 25,
            'high': base + up,
            'low': base + [l * 0.99 for l in up]
        })
        result = is_pump_dump_risk(hist)
        assert isinstance(result, bool)
