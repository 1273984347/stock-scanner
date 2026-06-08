"""ML 预测器 —— 加载训练好的 XGBoost/LightGBM 模型并提供推理

加载由 MLTrainer 训练的 XGBRanker/LGBMRanker 模型，
提供单样本和批量预测，合并多模型输出为综合评分。

依赖: xgboost, lightgbm, pandas, numpy
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import xgboost as xgb

from lib.ml_data import FACTOR_COLS
from lib.ml_trainer import MLTrainer

logger = logging.getLogger(__name__)

# 阈值：|score| > CONSENSUS_THRESHOLD 时才判定 BUY/SELL
CONSENSUS_THRESHOLD = 0.2


class MLPredictor:
    """ML 预测器 —— 加载模型并提供单样本/批量推理

    Parameters
    ----------
    model_dir : str
        模型存储目录，默认 "cache/models/"
    xgb_name : str, optional
        XGBoost 模型名称，默认 "xgb_20d_v1"（20日超额收益排序模型）
    lgbm_name : str, optional
        LightGBM 模型名称，默认 "lgbm_20d"
    """

    def __init__(
        self,
        model_dir: str = "cache/models/",
        xgb_name: str = "xgb_20d_v1",
        lgbm_name: str = "lgbm_20d",
    ):
        self.model_dir = model_dir

        self.xgb_model: Optional[xgb.Booster] = None
        self.xgb_manifest: Optional[Dict[str, Any]] = None
        self.lgbm_model: Any = None  # lightgbm.Booster
        self.lgbm_manifest: Optional[Dict[str, Any]] = None

        self._load_models(xgb_name, lgbm_name)

    # ============================================================
    #  模型加载
    # ============================================================

    def _load_models(self, xgb_name: str, lgbm_name: str) -> None:
        """加载 XGBoost 和 LightGBM 模型

        使用 MLTrainer.load_latest 加载最新版本，
        加载失败时不抛出异常，后续预测返回默认值。
        """
        trainer = MLTrainer(model_dir=self.model_dir)

        self.xgb_model, self.xgb_manifest = trainer.load_latest(xgb_name)
        self.lgbm_model, self.lgbm_manifest = trainer.load_latest(lgbm_name)

        if self.xgb_model is not None:
            logger.info("XGBoost 模型已加载: %s", xgb_name)
        if self.lgbm_model is not None:
            logger.info("LightGBM 模型已加载: %s", lgbm_name)
        if self.xgb_model is None and self.lgbm_model is None:
            logger.warning(
                "未加载任何模型，预测将不可用。"
                "请确认 %s 中存在已训练的模型。",
                self.model_dir,
            )

    # ============================================================
    #  属性
    # ============================================================

    @property
    def model_available(self) -> bool:
        """是否至少有一个模型可用"""
        return self.xgb_model is not None or self.lgbm_model is not None

    # ============================================================
    #  单样本预测
    # ============================================================

    def predict_single(self, factors: dict) -> dict:
        """对单一样本进行预测

        Parameters
        ----------
        factors : dict
            因子值字典，键为特征名（如 FACTOR_COLS），值为数值

        Returns
        -------
        dict
            - score: 综合评分 [-1, 1]
            - confidence: 置信度 [0, 1]
            - consensus: BUY / SELL / HOLD
            - top3_factors: 影响最大的 3 个因子
            - xgb_score: XGBoost 归一化评分
            - lgbm_score: LightGBM 归一化评分
            - model_available: 是否有可用模型
        """
        result: Dict[str, Any] = {
            "score": 0.0,
            "confidence": 0.0,
            "consensus": "HOLD",
            "top3_factors": [],
            "xgb_score": 0.0,
            "lgbm_score": 0.0,
            "model_available": self.model_available,
        }

        if not self.model_available:
            # §4.2 P1-2 修复 (2026-06-08, lib-incremental-audit §1.1):
            # 旧实现 silent return result (score=0, consensus=HOLD)
            # 生产中如果旧模型被加载, model_available=False, 所有预测退化为 HOLD
            # 风险: caller 看 score=0 误以为 '真信号', 实际是 '模型不可用'
            # 修复: logger.warning 提醒 + model_available=False 字段保留
            logger.warning(
                f"[MLPredictor] 模型 {self.xgb_name} 不可用 — 训练脚本未跑/路径错/版本不匹配, "
                f"所有预测退化为 HOLD, 禁止根据 score=0 误判"
            )
            return result

        feature_cols = self._get_feature_cols()
        if not feature_cols:
            logger.warning("缺少特征列定义，无法预测")
            return result

        # 构建特征向量（按 manifest 中定义的 feature_cols 顺序）
        feature_array = np.array(
            [[factors.get(col, 0.0) for col in feature_cols]],
            dtype=np.float32,
        )

        xgb_score = lgbm_score = 0.0
        count = 0

        if self.xgb_model is not None:
            try:
                dmat = xgb.DMatrix(feature_array, feature_names=feature_cols)
                raw = float(self.xgb_model.predict(dmat)[0])
                xgb_score = self._normalize(raw)
                count += 1
            except Exception as e:
                logger.warning("XGBoost 预测失败: %s", e)

        if self.lgbm_model is not None:
            try:
                raw = float(self.lgbm_model.predict(feature_array)[0])
                lgbm_score = self._normalize(raw)
                count += 1
            except Exception as e:
                logger.warning("LightGBM 预测失败: %s", e)

        combined = (xgb_score + lgbm_score) / count if count > 0 else 0.0

        # Top 3 因子
        importances = self._get_feature_importances()
        top3 = self._compute_top3_factors(factors, importances)

        # 决策
        if combined > CONSENSUS_THRESHOLD:
            consensus = "BUY"
        elif combined < -CONSENSUS_THRESHOLD:
            consensus = "SELL"
        else:
            consensus = "HOLD"

        result.update({
            "score": round(float(combined), 4),
            "confidence": round(float(abs(combined)), 4),
            "consensus": consensus,
            "top3_factors": top3,
            "xgb_score": round(float(xgb_score), 4),
            "lgbm_score": round(float(lgbm_score), 4),
        })
        return result

    # ============================================================
    #  批量预测
    # ============================================================

    def predict_batch(self, factor_df: pd.DataFrame) -> pd.Series:
        """对批量样本进行预测

        每个模型独立预测后取平均作为综合评分。

        Parameters
        ----------
        factor_df : pd.DataFrame
            因子 DataFrame，每行一个样本，列名为特征名

        Returns
        -------
        pd.Series
            每个样本的综合评分（归一化到 [-1, 1]），
            索引与 factor_df 保持一致
        """
        n = len(factor_df)

        if n == 0:
            return pd.Series([], index=factor_df.index, dtype=float)
        if not self.model_available:
            return pd.Series(np.zeros(n), index=factor_df.index)

        scores = np.zeros(n)
        count = 0

        if self.xgb_model is not None:
            try:
                dmat = xgb.DMatrix(factor_df)
                raw = self.xgb_model.predict(dmat)
                scores += self._normalize(raw)
                count += 1
            except Exception as e:
                logger.warning("XGBoost 批量预测失败: %s", e)

        if self.lgbm_model is not None:
            try:
                raw = self.lgbm_model.predict(factor_df)
                scores += self._normalize(raw)
                count += 1
            except Exception as e:
                logger.warning("LightGBM 批量预测失败: %s", e)

        if count > 0:
            scores /= count

        return pd.Series(scores, index=factor_df.index)

    # ============================================================
    #  模型时效检查
    # ============================================================

    def is_model_stale(self) -> bool:
        """检查训练好的模型是否已过期

        P0 #15 修复 (2026-06-08, lib-incremental-audit §1):
        - 旧实现用 manifest.train_date (模型保存到磁盘的 wall-clock 时间, line 268 `datetime.now().isoformat()`)
        - backtest 训练时 train_date 是回测当时 wall-clock, cache 永远是'最近训练', stale 检查永远 False
        - 生产: 旧模型被加载时 train_date 是 1 周前 → stale=True → predict 不可用, 但模型可能仍有效 (数据没变)
        - 修复: 加 manifest.train_data_end_date 字段 (训练数据最后日期)
          stale 检查改用 data_age = today - train_data_end_date > 7d
          实时回测都能正确判断 (回测: train_data_end_date = 回测当时; 生产: = 上次训练时)

        Returns
        -------
        bool
            模型是否过期（无可用模型时返回 True）
        """
        if not self.model_available:
            return True

        latest_data_end: Optional[datetime] = None
        latest_train: Optional[datetime] = None
        for manifest in (self.xgb_manifest, self.lgbm_manifest):
            if manifest is None:
                continue
            # P0 #15 修复: 优先用 train_data_end_date (数据截止), 回退 train_date (向后兼容)
            data_end_str = manifest.get("train_data_end_date")
            train_str = manifest.get("train_date")
            try:
                if data_end_str:
                    dt = datetime.fromisoformat(data_end_str)
                    if latest_data_end is None or dt > latest_data_end:
                        latest_data_end = dt
                if train_str:
                    dt = datetime.fromisoformat(train_str)
                    if latest_train is None or dt > latest_train:
                        latest_train = dt
            except (ValueError, TypeError):
                continue

        if latest_dt is None:
            return True

        age = datetime.now() - latest_dt
        return age > timedelta(days=7)

    # ============================================================
    #  内部工具
    # ============================================================

    @staticmethod
    def _normalize(raw: Union[float, np.ndarray]) -> Union[float, np.ndarray]:
        """将原始排名分数归一化到 [-1, 1]

        使用 tanh 将任意实数映射到 [-1, 1]。
        输入可以是标量或 ndarray，输出保持同样形状。
        """
        return np.tanh(np.asarray(raw, dtype=np.float64))

    def _get_feature_cols(self) -> List[str]:
        """获取特征列名

        优先级：XGBoost manifest -> LightGBM manifest -> FACTOR_COLS
        """
        if self.xgb_manifest and self.xgb_manifest.get("feature_cols"):
            return self.xgb_manifest["feature_cols"]
        if self.lgbm_manifest and self.lgbm_manifest.get("feature_cols"):
            return self.lgbm_manifest["feature_cols"]
        return list(FACTOR_COLS)

    def _get_feature_importances(self) -> Dict[str, float]:
        """合并 XGBoost 与 LightGBM 的特征重要性

        双模型均有重要性时取均值，单模型时直接使用。

        Returns
        -------
        Dict[str, float]
            特征名 -> 平均重要性分数
        """
        imp: Dict[str, float] = {}
        count_map: Dict[str, int] = {}

        if self.xgb_model is not None:
            try:
                for name, val in self.xgb_model.get_score(
                    importance_type="gain"
                ).items():
                    imp[name] = imp.get(name, 0.0) + val
                    count_map[name] = count_map.get(name, 0) + 1
            except Exception as e:
                logger.debug("XGBoost get_score 失败: %s", e)

        if self.lgbm_model is not None:
            try:
                lgbm_names = self.lgbm_model.feature_name()
                lgbm_vals = self.lgbm_model.feature_importance(
                    importance_type="gain"
                )
                for name, val in zip(lgbm_names, lgbm_vals):
                    imp[name] = imp.get(name, 0.0) + val
                    count_map[name] = count_map.get(name, 0) + 1
            except Exception as e:
                logger.debug("LightGBM feature_importance 失败: %s", e)

        # 平均（双模型贡献时除 2，单模型不除）
        for name in imp:
            imp[name] /= count_map.get(name, 1)

        return imp

    def _compute_top3_factors(
        self,
        factors: Dict[str, float],
        importances: Dict[str, float],
    ) -> List[Dict[str, Union[str, float]]]:
        """计算影响力最大的 3 个因子

        影响度 = 特征重要性 x |因子值|，按影响度降序排列。

        Parameters
        ----------
        factors : dict
            输入因子值
        importances : dict
            特征重要性（合并后）

        Returns
        -------
        list[dict]
            每个条目包含 name / value / impact
        """
        scored: List[Dict[str, Union[str, float]]] = []
        for name, value in factors.items():
            imp = importances.get(name, 0.0)
            impact = imp * abs(float(value))
            scored.append({
                "name": name,
                "value": float(value),
                "impact": round(impact, 6),
            })

        scored.sort(key=lambda x: x["impact"], reverse=True)  # type: ignore[typeddict-item]
        return scored[:3]


# ============================================================
#  快速验证
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    predictor = MLPredictor()
    print(f"MLPredictor 导入成功")
    print(f"模型可用: {predictor.model_available}")
    print(f"模型过期: {predictor.is_model_stale()}")
