"""评委策略抽象层

阶段1: 抽象出 JudgeStrategy 基类，为每位评委提供独立策略接口。
阶段2: 评委注册表配置化。
阶段3: JudgePanel 简化为编排器。

来源：wbh604/UZI-Skill (GitHub 1.5k stars)
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional
import pandas as pd


@dataclass
class JudgeResult:
    """评委投票结果"""
    judge: str
    group: str
    action: str  # BUY / SELL / HOLD
    confidence: float
    reason: str = ""


class JudgeStrategy(ABC):
    """评委策略抽象基类

    每位评委（无论人还是AI模型）都是 JudgeStrategy 的具体实现。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """评委名称"""
        pass

    @property
    @abstractmethod
    def group(self) -> str:
        """评委所属组别"""
        pass

    @property
    def weight(self) -> float:
        """评委权重（可在子类中覆盖）"""
        return 0.01

    @abstractmethod
    def evaluate(self, factors: dict, fundamentals: dict = None,
                 market_data: pd.DataFrame = None) -> JudgeResult:
        """执行评估

        Args:
            factors: 因子数据字典
            fundamentals: 基本面数据字典
            market_data: 市场数据 DataFrame

        Returns:
            JudgeResult: 投票结果
        """
        pass

    def _action_from_signal(self, signal: float, threshold_buy: float = 0.2,
                           threshold_sell: float = -0.2) -> str:
        """将信号值转换为动作

        Args:
            signal: 信号值 (通常 -1 到 1)
            threshold_buy: 买入阈值
            threshold_sell: 卖出阈值

        Returns:
            BUY/SELL/HOLD
        """
        if signal > threshold_buy:
            return "BUY"
        elif signal < threshold_sell:
            return "SELL"
        return "HOLD"


# ========== 经典价值派评委实现 ==========

class WarrenBuffettJudge(JudgeStrategy):
    """Warren Buffett 评委 - 低PE + 高ROE + 低负债"""

    @property
    def name(self) -> str:
        return "Warren Buffett"

    @property
    def group(self) -> str:
        return "经典价值"

    def evaluate(self, factors: dict, fundamentals: dict = None,
                 market_data: pd.DataFrame = None) -> JudgeResult:
        pe = factors.get("pe_ttm", 50)
        roe = (fundamentals.get("profitability", {}).get("ROE", 0) or 0) if fundamentals else 0
        debt = (fundamentals.get("solvency", {}).get("debt_to_asset", 0.5) or 0.5) if fundamentals else 0.5

        _has_roe = roe is not None and roe != 0

        if not _has_roe:
            # 无ROE时用因子代理
            _proxy_rsi = factors.get("rsi_14", 50)
            if pe < 20 and 40 <= _proxy_rsi <= 75:
                return JudgeResult(self.name, self.group, "BUY", 0.55,
                                  f"因子代理: PE={pe:.0f} RSI={_proxy_rsi:.1f} 合理")
            return JudgeResult(self.name, self.group, "HOLD", 0.45,
                              f"因子代理(无ROE): PE={pe:.0f} RSI={_proxy_rsi:.1f}")
        elif pe < 15 and roe > 0.15 and debt < 0.50:
            return JudgeResult(self.name, self.group, "BUY", 0.85,
                              f"PE={pe}<15, ROE={roe:.1%}>15%, 负债率低")
        elif pe > 30 or roe < 0.08:
            return JudgeResult(self.name, self.group, "SELL", 0.7,
                              f"PE={pe}>30 或 ROE={roe:.1%}<8%")
        return JudgeResult(self.name, self.group, "HOLD", 0.5)


class BenjaminGrahamJudge(JudgeStrategy):
    """Benjamin Graham 评委 - PE < 15, PB < 1.5"""

    @property
    def name(self) -> str:
        return "Benjamin Graham"

    @property
    def group(self) -> str:
        return "经典价值"

    def evaluate(self, factors: dict, fundamentals: dict = None,
                 market_data: pd.DataFrame = None) -> JudgeResult:
        pe = factors.get("pe_ttm", 50)
        pb = (fundamentals.get("per_share", {}).get("BVPS", 0) or 0) if fundamentals else 0
        _has_pb = pb is not None and pb != 0

        if not _has_pb:
            _proxy_vol = factors.get("volatility_20", 25)
            if pe < 15 and _proxy_vol < 35:
                return JudgeResult(self.name, self.group, "BUY", 0.50,
                                  f"因子代理: PE={pe:.0f}<15 波动率={_proxy_vol:.1f}")
            return JudgeResult(self.name, self.group, "HOLD", 0.45,
                              f"因子代理(无PB): PE={pe:.0f} 波动率={_proxy_vol:.1f}")
        elif pe < 15 and pb < 1.5:
            return JudgeResult(self.name, self.group, "BUY", 0.8,
                              f"PE={pe}<15, PB={pb:.2f}<1.5")
        return JudgeResult(self.name, self.group, "HOLD", 0.5)


class PeterLynchJudge(JudgeStrategy):
    """Peter Lynch 评委 - PEG 思路"""

    @property
    def name(self) -> str:
        return "Peter Lynch"

    @property
    def group(self) -> str:
        return "成长"

    def evaluate(self, factors: dict, fundamentals: dict = None,
                 market_data: pd.DataFrame = None) -> JudgeResult:
        pe = factors.get("pe_ttm", 50)
        mom_20 = factors.get("momentum_20", 0)

        if 0 < pe < 25 and mom_20 > 10:
            return JudgeResult(self.name, self.group, "BUY", 0.75,
                              f"PE={pe:.0f} 合理 + 20日动量={mom_20:.1f}%")
        elif pe > 50 and mom_20 < -5:
            return JudgeResult(self.name, self.group, "SELL", 0.7,
                              f"PE={pe:.0f} 偏高 + 动量下滑")
        return JudgeResult(self.name, self.group, "HOLD", 0.5)


class GeorgeSorosJudge(JudgeStrategy):
    """George Soros 评委 - 反身性"""

    @property
    def name(self) -> str:
        return "George Soros"

    @property
    def group(self) -> str:
        return "宏观对冲"

    def evaluate(self, factors: dict, fundamentals: dict = None,
                 market_data: pd.DataFrame = None) -> JudgeResult:
        mom_20 = factors.get("momentum_20", 0)
        rsi = factors.get("rsi_14", 50)

        if mom_20 > 15 and rsi > 70:
            return JudgeResult(self.name, self.group, "SELL", 0.7,
                              f"反身性见顶: 动量={mom_20:.1f}% RSI={rsi:.0f} 过热")
        elif mom_20 < -15 and rsi < 30:
            return JudgeResult(self.name, self.group, "BUY", 0.7,
                              f"反值性见底: 动量={mom_20:.1f}% RSI={rsi:.0f} 恐慌")
        return JudgeResult(self.name, self.group, "HOLD", 0.5)


class JesseLivermoreJudge(JudgeStrategy):
    """Jesse Livermore 评委 - 放量上涨"""

    @property
    def name(self) -> str:
        return "Jesse Livermore"

    @property
    def group(self) -> str:
        return "技术趋势"

    def evaluate(self, factors: dict, fundamentals: dict = None,
                 market_data: pd.DataFrame = None) -> JudgeResult:
        mom_5 = factors.get("momentum_5", 0)
        vol_ratio = factors.get("volume_ratio", 1.0)

        if mom_5 > 3 and vol_ratio > 1.5:
            return JudgeResult(self.name, self.group, "BUY", 0.8,
                              f"动量5={mom_5:.1f}%+量比{vol_ratio:.2f}")
        elif mom_5 < -5 and vol_ratio > 1.3:
            return JudgeResult(self.name, self.group, "SELL", 0.75,
                              f"破位放量: 动量={mom_5:.1f}%, 量比={vol_ratio:.2f}")
        return JudgeResult(self.name, self.group, "HOLD", 0.5)


# ========== 评委注册表 ==========

# 阶段2: 评委注册表配置化
JUDGE_REGISTRY = {
    "classic_value": [
        {"name": "Warren Buffett", "class": WarrenBuffettJudge, "weight": 0.04},
        {"name": "Benjamin Graham", "class": BenjaminGrahamJudge, "weight": 0.04},
    ],
    "growth": [
        {"name": "Peter Lynch", "class": PeterLynchJudge, "weight": 0.03},
    ],
    "macro": [
        {"name": "George Soros", "class": GeorgeSorosJudge, "weight": 0.03},
    ],
    "technical": [
        {"name": "Jesse Livermore", "class": JesseLivermoreJudge, "weight": 0.03},
    ],
}


def create_judge_instance(judge_class, factors, fundamentals, market_data) -> JudgeStrategy:
    """创建评委实例"""
    return judge_class()


def get_judges_by_preset(preset: str = "balanced") -> List[JudgeStrategy]:
    """根据预设获取评委列表

    Args:
        preset: balanced/aggressive/conservative

    Returns:
        评委实例列表
    """
    all_judges = []
    for group, judges in JUDGE_REGISTRY.items():
        for judge_cfg in judges:
            judge_class = judge_cfg["class"]
            all_judges.append(create_judge_instance(judge_class, None, None, None))
    return all_judges
