"""ML 在线预测监控 —— 追踪预测值与实际收益的偏差，计算滚动 IC，预警模型退化

使用 Spearman Rank IC（秩相关系数）评估模型预测能力。
监控每日预测评分与未来收益的相关性，在连续多日 IC 为负时触发预警。

依赖: numpy, scipy
"""

import logging
from typing import Any, Dict, List, Optional

import numpy as np
from scipy.stats import spearmanr

from config import MonitorConfig

logger = logging.getLogger(__name__)


class MLMonitor:
    """ML 预测监控器

    追踪每日预测评分与未来收益的秩相关性（IC），
    计算滚动 Spearman Rank IC，检测模型退化。

    Parameters
    ----------
    config : MonitorConfig or dict, optional
        监控配置，默认使用 MonitorConfig()。
        相关字段：
          - ic_window (int): 滚动 IC 窗口大小，默认 20
          - ic_alert_consecutive (int): 连续负 IC 预警阈值，默认 5
    """

    def __init__(self, config: Any = None):
        if config is None:
            config = MonitorConfig()

        # 兼容 dict 和 dataclass 两种输入
        if isinstance(config, dict):
            self.ic_window: int = config.get("ic_window", 20)
            self.alert_consecutive: int = config.get("ic_alert_consecutive", 5)
        else:
            self.ic_window: int = getattr(config, "ic_window", 20)
            self.alert_consecutive: int = getattr(config, "ic_alert_consecutive", 5)

        # 预测记录库：[{date, scores, future_returns}, ...]
        self.scores_db: List[Dict[str, Any]] = []

    # ============================================================
    #  记录与查询
    # ============================================================

    def record(self, date: str, scores: dict, future_returns: dict) -> None:
        """记录一次预测结果

        Parameters
        ----------
        date : str
            预测日期（如 "2026-05-14"）
        scores : dict
            预测评分字典，{股票代码: 预测评分}
        future_returns : dict
            实际未来收益字典，{股票代码: 实际收益}
        """
        self.scores_db.append({
            "date": date,
            "scores": dict(scores),
            "future_returns": dict(future_returns),
        })

        # 上限保护 —— 仅保留最近 10000 条记录
        if len(self.scores_db) > 10000:
            self.scores_db = self.scores_db[-10000:]

    def rolling_ic(self, window: int = None) -> float:
        """计算滚动 Spearman Rank IC

        取最近 window 个预测窗口内的所有 (评分, 收益) 对，
        计算 Spearman 秩相关系数。

        Parameters
        ----------
        window : int, optional
            窗口大小，默认使用 self.ic_window

        Returns
        -------
        float
            Spearman Rank IC，范围 [-1, 1]；数据不足时返回 0.0
        """
        if window is None:
            window = self.ic_window
        if window <= 0:
            return 0.0

        records = self.scores_db[-window:]
        if not records:
            return 0.0

        # 展开所有记录中的 (评分, 收益) 对
        all_scores: List[float] = []
        all_returns: List[float] = []
        for rec in records:
            for code, score in rec.get("scores", {}).items():
                ret = rec.get("future_returns", {}).get(code)
                if ret is not None:
                    all_scores.append(float(score))
                    all_returns.append(float(ret))

        if len(all_scores) < 3:
            logger.debug("滚动 IC 数据不足 (n=%d)", len(all_scores))
            return 0.0

        try:
            rho, _ = spearmanr(all_scores, all_returns)
            return float(rho) if not np.isnan(rho) else 0.0
        except Exception as e:
            logger.warning("IC 计算失败: %s", e)
            return 0.0

    # ============================================================
    #  退化预警
    # ============================================================

    def alert(self) -> Optional[str]:
        """检查模型退化预警

        如果最近连续 self.alert_consecutive 个周期
        的 IC 均为负值，返回预警消息。

        Returns
        -------
        str or None
            预警消息文本，无预警时返回 None
        """
        if len(self.scores_db) < self.alert_consecutive:
            return None

        recent = self.scores_db[-self.alert_consecutive:]
        for rec in recent:
            scores = rec.get("scores", {})
            returns = rec.get("future_returns", {})

            period_scores: List[float] = []
            period_returns: List[float] = []
            for code, score in scores.items():
                ret = returns.get(code)
                if ret is not None:
                    period_scores.append(float(score))
                    period_returns.append(float(ret))

            # 单周期内不足以计算有意义的 IC
            if len(period_scores) < 3:
                # §4.2 P1 修复 (2026-06-08, lib-incremental-audit §1.2):
                # 旧直接 return None — 数据少就闭嘴, 模型退化在低流动性日 (<3 只股票有预测)
                # 会被静默吞掉, 累计 0 个负 IC 中断连续序列
                # 修复: logger.debug 记录样本不足, 继续到下一周期
                logger.debug(f"[ml_monitor.alert] 周期内样本不足 ({len(period_scores)} < 3), 跳过本周期, 继续累积")
                continue

            try:
                rho, _ = spearmanr(period_scores, period_returns)
                if np.isnan(rho) or rho >= 0:
                    # 非负 IC 中断连续序列
                    return None
            except Exception as e:
                logger.warning("预警 IC 计算失败: %s", e)
                return None

        # 所有连续周期 IC 均为负
        msg = (
            f"ML 模型退化预警: 连续 {self.alert_consecutive} "
            f"个周期 IC 为负"
        )
        logger.warning(msg)
        return msg


# ============================================================
#  快速验证
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    monitor = MLMonitor()
    print(f"MLMonitor 导入成功")
    print(f"IC 窗口: {monitor.ic_window}, 预警连续数: {monitor.alert_consecutive}")
