"""数据修正器测试"""
import copy

import pytest
from lib.data.data_correction import DataCorrection


class TestDataCorrection:
    @pytest.fixture(autouse=True)
    def _hermetic(self, monkeypatch):
        """测试不读写真实 data_corrections.json（40KB 运行态文件）。

        根因：DataCorrection.__init__/_save_corrections 直接落盘到仓库根目录，
        既有 3 个测试会污染该文件。此处从类级别切断 IO，断言不变、全程 hermetic。
        """
        monkeypatch.setattr(DataCorrection, "_load_corrections", lambda self: None)
        monkeypatch.setattr(DataCorrection, "_save_corrections", lambda self: None)

    def _make(self, tmp_path):
        dc = DataCorrection()
        dc._corrections = []
        return dc

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

    # ---- P1-10 收尾：correct_data 的「不改入参」契约 ----

    def test_correct_data_does_not_mutate_input(self, tmp_path):
        """【红→绿守卫】correct_data 不得就地篡改调用方传入的 data。

        根因：corrected = data.copy() 是 list 浅拷贝，元素是同一批 dict；
        写回 corrected[idx]["close"] 会同时改掉 data[idx]，调用方拿到的
        「原始历史」被静默污染（尤其修正缓存时，原始值应保留用于审计）。
        """
        dc = self._make(tmp_path)
        data = [
            {"date": "2024-01-01", "close": 100, "volume": 1000},
            {"date": "2024-01-02", "close": 100, "volume": 1000},
            {"date": "2024-01-03", "close": 100, "volume": 1000},
            {"date": "2024-01-04", "close": 300, "volume": 1000},  # 跳变，需修正
            {"date": "2024-01-05", "close": 50, "volume": 1000},   # next 不确认
        ]
        snapshot = copy.deepcopy(data)
        anomalies = dc.detect_anomalies(data)

        corrected = dc.correct_data(data, anomalies)

        assert data == snapshot, "correct_data 污染了入参 data（浅拷贝共享 dict）"
        assert corrected[3] is not data[3], "返回行与入参行共享了同一个 dict 对象"
        assert corrected[3]["close"] == pytest.approx(100), "修正值应为前 3 日均值 100"

    def test_volume_spike_does_not_mutate_input(self, tmp_path):
        """【红→绿守卫】volume_spike 修正同样不得污染入参。"""
        dc = self._make(tmp_path)
        data = [
            {"date": "2024-01-01", "close": 100, "volume": 1000},
            {"date": "2024-01-02", "close": 100, "volume": 999999},  # 成交量异常
            {"date": "2024-01-03", "close": 100, "volume": 1000},
        ]
        snapshot = copy.deepcopy(data)
        anomalies = [{
            "type": "volume_spike", "index": 1, "date": "2024-01-02",
            "volume": 999999, "mean_volume": 1000.0, "z_score": 9.9,
        }]

        corrected = dc.correct_data(data, anomalies)

        assert data == snapshot, "volume_spike 修正污染了入参 data"
        assert corrected[1] is not data[1]
        assert corrected[1]["volume"] == pytest.approx(1000.0)

    def test_confirms_real_continuation_skips_correction(self, tmp_path):
        """契约守卫：次日收盘确认新价位 → 判定真实延续，跳过修正。

        这里读 next_close 是「事后回溯修正」的必要信息（拿后一天确认跳变真伪），
        不是回测信号的前视偏差。去掉它会退回 P0-2「抹平真实涨停/复牌」。
        """
        dc = self._make(tmp_path)
        data = [
            {"date": "2024-01-01", "close": 100, "volume": 1000},
            {"date": "2024-01-02", "close": 100, "volume": 1000},
            {"date": "2024-01-03", "close": 300, "volume": 1000},  # 跳变
            {"date": "2024-01-04", "close": 310, "volume": 1000},  # 次日≈当日
            {"date": "2024-01-05", "close": 305, "volume": 1000},
        ]
        anomalies = dc.detect_anomalies(data)
        assert any(a["type"] == "price_jump" for a in anomalies)

        corrected = dc.correct_data(data, anomalies)

        assert corrected[2]["close"] == 300, "真实价格延续不应被修正"
        assert any(c["type"] == "price_jump_confirmed" for c in dc._corrections)

    def test_limit_move_above_threshold_skips_correction(self, tmp_path):
        """契约守卫：变化率命中 A 股涨跌停阈值（30%）→ 真实涨停，跳过修正。"""
        dc = self._make(tmp_path)
        data = [
            {"date": "2024-01-01", "close": 100, "volume": 1000},
            {"date": "2024-01-02", "close": 130, "volume": 1000},  # +30% 涨停
            {"date": "2024-01-03", "close": 50, "volume": 1000},
            {"date": "2024-01-04", "close": 50, "volume": 1000},
        ]
        anomalies = dc.detect_anomalies(data)
        assert any(a["type"] == "price_jump" for a in anomalies)

        corrected = dc.correct_data(data, anomalies)

        assert corrected[1]["close"] == 130, "真实涨跌停不应被修正"
        assert any(c["type"] == "price_jump_confirmed" for c in dc._corrections)
