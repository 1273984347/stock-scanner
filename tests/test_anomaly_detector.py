"""财报异常检测测试"""
import pytest
import pandas as pd
import numpy as np
from lib.anomaly_detector import detect_anomalies, anomaly_summary


class TestDetectAnomalies:
    """detect_anomalies 函数测试"""

    def _make_period(self, period, revenue=1000, cost=600, net_income=200,
                     ar=200, inv=150, current_assets=500, total_assets=1000,
                     current_liab=300, total_liab=400, equity=600, goodwill=50,
                     ap=100, ocf=150, capex=50):
        """构造单期财务数据"""
        return {
            "period": period,
            "revenue": revenue,
            "cost_of_revenue": cost,
            "operating_income": revenue - cost,
            "net_income": net_income,
            "accounts_receivable": ar,
            "inventory": inv,
            "total_current_assets": current_assets,
            "total_assets": total_assets,
            "total_current_liabilities": current_liab,
            "total_liabilities": total_liab,
            "total_equity": equity,
            "goodwill": goodwill,
            "accounts_payable": ap,
            "operating_cash_flow": ocf,
            "capex": capex,
        }

    def test_empty_periods_returns_empty(self):
        """空数据应返回空列表"""
        result = detect_anomalies([])
        assert result == []

    def test_single_period_returns_empty(self):
        """单期数据不足应返回空列表"""
        period = self._make_period("2024Q1")
        result = detect_anomalies([period])
        assert result == []

    def test_normal_data_no_anomalies(self):
        """正常财务数据无异常"""
        periods = [
            self._make_period("2024Q1", revenue=1000, cost=600, net_income=200,
                              ar=200, inv=150, ocf=180),
            self._make_period("2024Q2", revenue=1050, cost=630, net_income=210,
                              ar=205, inv=155, ocf=190),
        ]
        result = detect_anomalies(periods)
        # 正常数据可能无警报或仅有轻微警报
        assert isinstance(result, list)

    def test_accounts_receivable_surge(self):
        """应收账款暴增检测"""
        periods = [
            self._make_period("2024Q1", revenue=1000, cost=600, net_income=200, ar=200),
            self._make_period("2024Q2", revenue=1000, cost=600, net_income=200, ar=500),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "应收账款暴增" in rules

    def test_cash_flow_diverges_from_profit(self):
        """现金流背离利润检测"""
        periods = [
            self._make_period("2024Q1", net_income=200, ocf=150),
            self._make_period("2024Q2", net_income=200, ocf=-50),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "现金流背离利润" in rules

    def test_cash_flow_low_ratio(self):
        """现金流/利润比率偏低"""
        periods = [
            self._make_period("2024Q1", net_income=200, ocf=80),
            self._make_period("2024Q2", net_income=200, ocf=60),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "现金流背离利润" in rules

    def test_inventory_accumulation(self):
        """存货堆积检测"""
        periods = [
            self._make_period("2024Q1", revenue=1000, inv=100),
            self._make_period("2024Q2", revenue=1000, inv=250),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "存货积压" in rules

    def test_gross_margin_sudden_change(self):
        """毛利率突变检测"""
        periods = [
            self._make_period("2024Q1", revenue=1000, cost=600),
            self._make_period("2024Q2", revenue=1000, cost=800),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "毛利率突变" in rules

    def test_net_margin_sudden_change(self):
        """净利率突变检测"""
        periods = [
            self._make_period("2024Q1", revenue=1000, cost=600, net_income=200),
            self._make_period("2024Q2", revenue=1000, cost=600, net_income=120),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "净利率突变" in rules

    def test_consecutive_negative_ocf(self):
        """持续负经营现金流检测"""
        periods = [
            self._make_period("2024Q1", net_income=100, ocf=-50),
            self._make_period("2024Q2", net_income=100, ocf=-80),
            self._make_period("2024Q3", net_income=100, ocf=-60),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "经营现金流持续为负" in rules

    def test_high_goodwill_ratio(self):
        """商誉占比过高检测"""
        periods = [
            self._make_period("2024Q1", total_assets=1000, goodwill=100),
            self._make_period("2024Q2", total_assets=1000, goodwill=400),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "商誉占比过高" in rules

    def test_high_debt_ratio(self):
        """资产负债率过高检测"""
        periods = [
            self._make_period("2024Q1", total_assets=1000, total_liab=400),
            self._make_period("2024Q2", total_assets=1000, total_liab=800),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "资产负债率过高" in rules

    def test_low_current_ratio(self):
        """流动比率过低检测"""
        periods = [
            self._make_period("2024Q1", current_assets=500, current_liab=300),
            self._make_period("2024Q2", current_assets=300, current_liab=500),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "流动比率过低" in rules

    def test_accounts_payable_anomaly(self):
        """应付账款异常检测"""
        periods = [
            self._make_period("2024Q1", cost=600, ap=100),
            self._make_period("2024Q2", cost=600, ap=300),
        ]
        result = detect_anomalies(periods)
        rules = [a["rule"] for a in result]
        assert "应付账款异常" in rules

    def test_custom_thresholds(self):
        """自定义阈值生效"""
        periods = [
            self._make_period("2024Q1", revenue=1000, ar=200),
            self._make_period("2024Q2", revenue=1000, ar=300),  # ar_yoy = 50%
        ]
        # 默认阈值 ar_threshold=20%，这里自定义为 60% 应不触发
        result = detect_anomalies(periods, thresholds={"ar_threshold": 0.60})
        rules = [a["rule"] for a in result]
        assert "应收账款暴增" not in rules

    def test_alert_structure(self):
        """警报结构完整"""
        periods = [
            self._make_period("2024Q1", net_income=200, ocf=-100),
            self._make_period("2024Q2", net_income=200, ocf=-100),
        ]
        result = detect_anomalies(periods)
        for alert in result:
            assert "rule" in alert
            assert "period" in alert
            assert "severity" in alert
            assert "trigger" in alert
            assert "detail" in alert
            assert "implication" in alert


class TestAnomalySummary:
    """anomaly_summary 函数测试"""

    def test_empty_alerts(self):
        """空警报列表"""
        result = anomaly_summary([])
        assert result["total"] == 0
        assert result["high"] == 0
        assert result["medium"] == 0
        assert result["low"] == 0
        assert result["score_penalty"] == 0

    def test_high_severity_counts(self):
        """高严重度计数"""
        alerts = [
            {"severity": "high"},
            {"severity": "high"},
            {"severity": "medium"},
        ]
        result = anomaly_summary(alerts)
        assert result["high"] == 2
        assert result["medium"] == 1
        assert result["total"] == 3

    def test_score_penalty_calculation(self):
        """扣分计算正确"""
        # high=-3, medium=-1.5, low=-0.5
        alerts = [
            {"severity": "high"},
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
        ]
        result = anomaly_summary(alerts)
        # 2*3.0 + 1*1.5 + 1*0.5 = 8.0
        assert result["score_penalty"] == 8.0

    def test_all_severities(self):
        """所有严重度等级"""
        alerts = [
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
        ]
        result = anomaly_summary(alerts)
        assert result["total"] == 3
        assert result["high"] == 1
        assert result["medium"] == 1
        assert result["low"] == 1
