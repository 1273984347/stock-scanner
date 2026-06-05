"""历史数据回溯修正 — 发现数据错误时自动修正缓存

功能：
- 检测数据异常（价格跳变、成交量异常、缺失数据）
- 自动修正缓存中的错误数据
- 记录修正历史
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 修正记录文件
CORRECTION_LOG = Path("data_corrections.json")

# 异常检测阈值
PRICE_JUMP_THRESHOLD = 0.20  # 价格跳变超过 20% 视为异常
VOLUME_SPIKE_THRESHOLD = 5.0  # 成交量超过 5 倍均值视为异常
MISSING_DATA_THRESHOLD = 0.1  # 缺失数据超过 10% 视为异常

# A 股涨跌停阈值（含 ST）
# 主板 ±10% / 创业板/科创板 ±20% / 北交所 ±30% / ST ±5%
A_SHARE_LIMIT_THRESHOLDS = [0.05, 0.10, 0.20, 0.30]
LIMIT_TOLERANCE = 0.005  # 0.5% 容差，匹配实际涨跌停精度


class DataCorrection:
    """数据修正器"""

    def __init__(self):
        self._corrections = []
        self._load_corrections()

    def _load_corrections(self):
        """加载修正记录"""
        if CORRECTION_LOG.exists():
            try:
                with open(CORRECTION_LOG, "r", encoding="utf-8") as f:
                    self._corrections = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load corrections: {e}")

    def _save_corrections(self):
        """保存修正记录"""
        try:
            with open(CORRECTION_LOG, "w", encoding="utf-8") as f:
                json.dump(self._corrections, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save corrections: {e}")

    def detect_anomalies(self, data: List[Dict]) -> List[Dict]:
        """检测数据异常"""
        anomalies = []

        if len(data) < 2:
            return anomalies

        # 检测价格跳变
        for i in range(1, len(data)):
            prev_close = data[i-1].get("close", 0)
            curr_close = data[i].get("close", 0)

            if prev_close > 0:
                change = abs(curr_close - prev_close) / prev_close
                if change > PRICE_JUMP_THRESHOLD:
                    anomalies.append({
                        "type": "price_jump",
                        "index": i,
                        "date": data[i].get("date"),
                        "prev_close": prev_close,
                        "curr_close": curr_close,
                        "change": change,
                        "threshold": PRICE_JUMP_THRESHOLD
                    })

        # 检测成交量异常
        volumes = [d.get("volume", 0) for d in data]
        mean_volume = np.mean(volumes)
        std_volume = np.std(volumes)

        for i, vol in enumerate(volumes):
            if std_volume > 0:
                z_score = (vol - mean_volume) / std_volume
                if z_score > VOLUME_SPIKE_THRESHOLD:
                    anomalies.append({
                        "type": "volume_spike",
                        "index": i,
                        "date": data[i].get("date"),
                        "volume": vol,
                        "mean_volume": mean_volume,
                        "z_score": z_score
                    })

        # 检测缺失数据
        dates = [d.get("date") for d in data]
        if dates:
            date_range = pd.date_range(min(dates), max(dates))
            missing_count = len(date_range) - len(dates)
            missing_ratio = missing_count / len(date_range) if len(date_range) > 0 else 0

            if missing_ratio > MISSING_DATA_THRESHOLD:
                anomalies.append({
                    "type": "missing_data",
                    "missing_count": missing_count,
                    "missing_ratio": missing_ratio,
                    "threshold": MISSING_DATA_THRESHOLD
                })

        return anomalies

    def correct_data(self, data: List[Dict], anomalies: List[Dict]) -> List[Dict]:
        """修正数据"""
        corrected = data.copy()
        corrections = []

        for anomaly in anomalies:
            if anomaly["type"] == "price_jump":
                # P0-2 修复（2026-06-05）：不再无脑抹平 OHLC
                # 旧逻辑把 open/high/low/close 全部改成 (prev+next)/2，会破坏真实涨跌停/复牌/分红
                # 新逻辑：
                #   1) 变化率在 A 股涨跌停阈值附近 → 真实涨跌停，跳过
                #   2) 下一日收盘确认新价位（next ≈ curr）→ 真实延续，跳过
                #   3) 否则才是数据错误，只修正 close（保留 OHLC — 高/低/开可能是真信号）
                idx = anomaly["index"]
                if idx > 0 and idx < len(corrected) - 1:
                    prev_close = corrected[idx-1].get("close", 0)
                    curr_close = corrected[idx].get("close", 0)
                    next_close = corrected[idx+1].get("close", 0) if idx < len(corrected) - 1 else prev_close

                    is_limit = False
                    if prev_close > 0:
                        change_pct = abs(curr_close - prev_close) / prev_close
                        for limit in A_SHARE_LIMIT_THRESHOLDS:
                            if abs(change_pct - limit) < LIMIT_TOLERANCE:
                                is_limit = True
                                break

                    confirms = (
                        curr_close > 0
                        and abs(next_close - curr_close) / curr_close < 0.05
                    )

                    if is_limit or confirms:
                        corrections.append({
                            "type": "price_jump_confirmed",
                            "index": idx,
                            "date": corrected[idx].get("date"),
                            "change": anomaly["change"],
                            "reason": (
                                f"涨跌停/价格延续（{anomaly['change']:.2%}）— 跳过修正"
                                if is_limit
                                else f"下一日确认新价位— 跳过修正"
                            ),
                        })
                        continue

                    corrected_value = (prev_close + next_close) / 2
                    corrections.append({
                        "type": "price_jump",
                        "index": idx,
                        "date": corrected[idx].get("date"),
                        "original": curr_close,
                        "corrected": corrected_value,
                        "reason": f"价格跳变 {anomaly['change']:.2%}，next 不确认，判定数据错误"
                    })
                    corrected[idx]["close"] = corrected_value

            elif anomaly["type"] == "volume_spike":
                # 使用均值修正
                idx = anomaly["index"]
                corrected_value = anomaly["mean_volume"]

                corrections.append({
                    "type": "volume_spike",
                    "index": idx,
                    "date": corrected[idx].get("date"),
                    "original": corrected[idx].get("volume"),
                    "corrected": corrected_value,
                    "reason": f"成交量异常 Z={anomaly['z_score']:.1f}"
                })

                corrected[idx]["volume"] = corrected_value

        # 记录修正
        if corrections:
            self._corrections.extend(corrections)
            self._save_corrections()
            logger.info(f"Applied {len(corrections)} corrections")

        return corrected

    def get_corrections(self, limit: int = 100) -> List[Dict]:
        """获取修正记录"""
        return self._corrections[-limit:]

    def get_correction_stats(self) -> Dict:
        """获取修正统计"""
        if not self._corrections:
            return {"total": 0, "by_type": {}}

        by_type = {}
        for c in self._corrections:
            t = c.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total": len(self._corrections),
            "by_type": by_type,
            "last_correction": self._corrections[-1] if self._corrections else None
        }


# 单例
data_corrector = DataCorrection()
