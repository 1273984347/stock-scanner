"""模型 A/B 测试框架 —— 多模型同时运行对比

支持同时加载多个模型变体，在相同测试数据上运行预测，
对比 Rank IC、方向准确率等指标，选出最优模型。

使用方式：
    runner = ABTestRunner("xgb_vs_lgbm_v2")
    runner.add_model("xgb_v1", predictor_xgb)
    runner.add_model("lgbm_v1", predictor_lgbm)
    result = runner.run(test_data, actual_returns)
    print(result.winner)

依赖: numpy, scipy
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

AB_TEST_DIR = Path("cache/ab_tests")


@dataclass
class ABTestResult:
    """A/B 测试结果"""

    test_name: str
    models: List[str]
    n_samples: int = 0
    # 每个模型的指标
    model_metrics: Dict[str, Dict] = field(default_factory=dict)
    # 排名
    winner: str = ""
    timestamp: str = ""


class ABTestRunner:
    """A/B 测试运行器

    Parameters
    ----------
    test_name : str
        测试名称，用于结果文件命名

    使用方式：
        runner = ABTestRunner("test_v1")
        runner.add_model("xgb_v1", predictor_xgb)
        runner.add_model("lgbm_v1", predictor_lgbm)
        result = runner.run(test_data, actual_returns)
    """

    def __init__(self, test_name: str):
        self.test_name = test_name
        self.models: Dict[str, Any] = {}
        AB_TEST_DIR.mkdir(parents=True, exist_ok=True)

    def add_model(self, name: str, predictor: Any) -> None:
        """添加模型

        Parameters
        ----------
        name : str
            模型名称（如 "xgb_v1", "lgbm_v2"）
        predictor : object
            预测器对象，需提供 predict(data) 方法
        """
        self.models[name] = predictor

    def run(
        self,
        test_data: Any,
        actual_returns: Optional[Dict[str, float]] = None,
    ) -> ABTestResult:
        """运行 A/B 测试

        Parameters
        ----------
        test_data : DataFrame or dict
            测试数据
        actual_returns : dict, optional
            {code: actual_return} 实际收益，用于计算 IC 等指标

        Returns
        -------
        ABTestResult
        """
        if not self.models:
            return ABTestResult(test_name=self.test_name, models=[])

        model_metrics: Dict[str, Dict] = {}

        for name, predictor in self.models.items():
            try:
                predictions = predictor.predict(test_data)
                metrics = self._compute_metrics(predictions, actual_returns)
                model_metrics[name] = metrics
            except Exception as e:
                logger.warning("模型 %s 预测失败: %s", name, e)
                model_metrics[name] = {"error": str(e)}

        winner = self._select_winner(model_metrics)

        result = ABTestResult(
            test_name=self.test_name,
            models=list(self.models.keys()),
            n_samples=len(test_data) if hasattr(test_data, "__len__") else 0,
            model_metrics=model_metrics,
            winner=winner,
            timestamp=datetime.now().isoformat(),
        )

        self._save_result(result)
        return result

    def _compute_metrics(
        self,
        predictions: Any,
        actual_returns: Optional[Dict[str, float]],
    ) -> Dict:
        """计算模型指标

        Returns
        -------
        dict
            包含 rank_ic / pvalue / accuracy / n_samples
        """
        if not actual_returns:
            pred_count = len(predictions) if hasattr(predictions, "__len__") else 0
            return {"predictions_count": pred_count}

        from scipy.stats import spearmanr

        # 对齐预测和实际收益
        pred_list: List[float] = []
        actual_list: List[float] = []

        if isinstance(predictions, dict):
            items = predictions.items()
        elif hasattr(predictions, "items"):
            items = predictions.items()
        else:
            return {"error": "unsupported prediction type"}

        for code, pred in items:
            if code in actual_returns:
                pred_list.append(float(pred))
                actual_list.append(float(actual_returns[code]))

        if len(pred_list) < 3:
            return {"error": "insufficient aligned data", "n_aligned": len(pred_list)}

        pred_arr = np.array(pred_list)
        actual_arr = np.array(actual_list)

        # Rank IC (Spearman)
        ic, pvalue = spearmanr(pred_arr, actual_arr)

        # 方向准确率（预测方向与实际方向一致）
        correct = sum(
            1 for p, a in zip(pred_arr, actual_arr) if (p > 0) == (a > 0)
        )
        accuracy = correct / len(pred_arr)

        return {
            "rank_ic": round(float(ic), 4) if not np.isnan(ic) else 0.0,
            "pvalue": round(float(pvalue), 4) if not np.isnan(pvalue) else 1.0,
            "accuracy": round(accuracy, 4),
            "n_samples": len(pred_arr),
        }

    @staticmethod
    def _select_winner(model_metrics: Dict[str, Dict]) -> str:
        """选出最优模型（按 Rank IC 降序）"""
        best_name = ""
        best_ic = -2.0  # IC 范围 [-1, 1]

        for name, metrics in model_metrics.items():
            ic = metrics.get("rank_ic", -2.0)
            if ic > best_ic:
                best_ic = ic
                best_name = name

        return best_name

    def _save_result(self, result: ABTestResult) -> None:
        """保存测试结果到 JSON"""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file = AB_TEST_DIR / f"{result.test_name}_{ts}.json"
            data = {
                "test_name": result.test_name,
                "models": result.models,
                "n_samples": result.n_samples,
                "model_metrics": result.model_metrics,
                "winner": result.winner,
                "timestamp": result.timestamp,
            }
            file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.info("A/B 测试结果已保存: %s", file)
        except Exception as e:
            logger.debug("A/B 测试结果保存失败: %s", e)


# ============================================================
#  快速验证
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    runner = ABTestRunner("smoke_test")
    print(f"ABTestRunner 导入成功")
    print(f"结果目录: {AB_TEST_DIR}")
