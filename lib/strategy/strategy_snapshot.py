"""策略参数快照 — 保存和恢复策略配置

功能：
- 快照内容：因子权重 + 出场天数 + 止损止盈 + 策略权重 + regime 配置
- 存储：snapshots/ 目录，JSON 格式，含时间戳
- 操作：保存 / 列表 / 恢复 / 删除
"""

import json
import os
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# 快照存储目录
SNAPSHOT_DIR = Path(__file__).parent.parent.parent / "snapshots"


@dataclass
class StrategySnapshot:
    """策略快照"""
    id: str
    name: str
    description: str
    timestamp: str
    # 策略参数
    factor_weights: Dict[str, float]
    exit_days: Dict[str, int]
    stop_loss: float
    stop_profit: float
    strategy_weights: Dict[str, float]
    regime_config: Dict[str, any]
    # 元数据
    preset: str
    backtest_result: Optional[Dict] = None


class SnapshotManager:
    """快照管理器"""

    def __init__(self):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, description: str = "", params: Dict = None) -> StrategySnapshot:
        """保存当前策略配置为快照"""
        snapshot_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 获取当前配置
        if params is None:
            params = self._get_current_config()

        snapshot = StrategySnapshot(
            id=snapshot_id,
            name=name,
            description=description,
            timestamp=datetime.now().isoformat(),
            factor_weights=params.get("factor_weights", {}),
            exit_days=params.get("exit_days", {}),
            stop_loss=params.get("stop_loss", 2.0),
            stop_profit=params.get("stop_profit", 4.0),
            strategy_weights=params.get("strategy_weights", {}),
            regime_config=params.get("regime_config", {}),
            preset=params.get("preset", "balanced"),
            backtest_result=params.get("backtest_result")
        )

        # 保存到文件
        filepath = SNAPSHOT_DIR / f"{snapshot_id}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(asdict(snapshot), f, ensure_ascii=False, indent=2)

        logger.info(f"Snapshot saved: {snapshot_id} - {name}")
        return snapshot

    def list(self) -> List[StrategySnapshot]:
        """列出所有快照"""
        snapshots = []
        for filepath in sorted(SNAPSHOT_DIR.glob("*.json"), reverse=True):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    snapshots.append(StrategySnapshot(**data))
            except Exception as e:
                logger.warning(f"Failed to load snapshot {filepath}: {e}")
        return snapshots

    def get(self, snapshot_id: str) -> Optional[StrategySnapshot]:
        """获取指定快照"""
        filepath = SNAPSHOT_DIR / f"{snapshot_id}.json"
        if not filepath.exists():
            return None

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return StrategySnapshot(**data)
        except Exception as e:
            logger.error(f"Failed to load snapshot {snapshot_id}: {e}")
            return None

    def restore(self, snapshot_id: str) -> Dict:
        """恢复快照配置"""
        snapshot = self.get(snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot not found: {snapshot_id}")

        # 返回配置，由调用方应用
        return {
            "factor_weights": snapshot.factor_weights,
            "exit_days": snapshot.exit_days,
            "stop_loss": snapshot.stop_loss,
            "stop_profit": snapshot.stop_profit,
            "strategy_weights": snapshot.strategy_weights,
            "regime_config": snapshot.regime_config,
            "preset": snapshot.preset
        }

    def delete(self, snapshot_id: str) -> bool:
        """删除快照"""
        filepath = SNAPSHOT_DIR / f"{snapshot_id}.json"
        if not filepath.exists():
            return False

        try:
            filepath.unlink()
            logger.info(f"Snapshot deleted: {snapshot_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete snapshot {snapshot_id}: {e}")
            return False

    def _get_current_config(self) -> Dict:
        """获取当前策略配置"""
        try:
            from config import FactorConfig, RiskConfig
            return {
                "factor_weights": FactorConfig.ic_factor_weights or {},
                "exit_days": {
                    "TREND_UP": 10,
                    "MEAN_REVERSION": 5,
                    "TREND_DOWN": 3
                },
                "stop_loss": RiskConfig.stop_loss_atr if hasattr(RiskConfig, 'stop_loss_atr') else 2.0,
                "stop_profit": RiskConfig.stop_profit_atr if hasattr(RiskConfig, 'stop_profit_atr') else 4.0,
                "strategy_weights": FactorConfig.strategy_weights or {},
                "regime_config": {},
                "preset": "balanced"
            }
        except ImportError:
            logger.warning("Config not available, using defaults")
            return {
                "factor_weights": {},
                "exit_days": {},
                "stop_loss": 2.0,
                "stop_profit": 4.0,
                "strategy_weights": {},
                "regime_config": {},
                "preset": "balanced"
            }


# 单例
snapshot_manager = SnapshotManager()
