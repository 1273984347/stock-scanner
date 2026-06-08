"""ML 模型训练：XGBRanker + LGBMRanker，含超参搜索与模型序列化

训练日频截面排序模型，用于 A 股多空排序预测。
输出模型版本化保存到 cache/models/，支持滚动更新与回滚。

依赖: xgboost, lightgbm, scikit-learn
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import lightgbm as lgb
import numpy as np
import xgboost as xgb
from sklearn.model_selection import ParameterGrid, TimeSeriesSplit

from config import MLConfig

logger = logging.getLogger(__name__)

# 训练/搜索默认参数（被 train_* / _grid_search_* 共用）
# §4.2 A3 迁移 (2026-06-08): 原模块级常量 → config.MLConfig.default_xgb_params
DEFAULT_XGB_PARAMS: Dict[str, Any] = MLConfig().default_xgb_params

DEFAULT_LGBM_PARAMS: Dict[str, Any] = {
    "objective": "lambdarank",
    "metric": "ndcg",
    "learning_rate": 0.1,
    "num_leaves": 31,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "seed": 42,
    "verbose": -1,
}

# LightGBM lambdarank 标签分级上限（覆盖 0..199 共 200 档）
MAX_LABEL_GAIN: int = 200


class _TimeSeriesSplitWithEmbargo:
    """P0-5: TimeSeriesSplit with embargo gap between train and val.

    之前 grid search 用 nfold=3（随机 K-Fold）—— 标签泄漏到训练折
    现在用 TimeSeriesSplit + embargo（前视偏差防护 + 标签窗口不重叠）
    """
    def __init__(self, n_splits: int = 3, embargo_gap: int = 5):
        self.n_splits = n_splits
        self.embargo_gap = embargo_gap
        self._inner = TimeSeriesSplit(n_splits=n_splits)

    def split(self, X, y=None, groups=None):
        for train_idx, val_idx in self._inner.split(X):
            if len(train_idx) > self.embargo_gap:
                train_idx = train_idx[:-self.embargo_gap]
            else:
                train_idx = train_idx[:0]
            yield train_idx, val_idx

    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits

MODEL_DIR = Path(__file__).resolve().parent.parent / "cache" / "models"


class MLTrainer:
    """XGBRanker / LGBMRanker 训练器

    Parameters
    ----------
    model_dir : str, optional
        模型存储目录，默认 cache/models/
    """

    def __init__(self, model_dir: Optional[str] = None):
        """初始化模型训练器

        Parameters
        ----------
        model_dir : str, optional
            模型存储目录，默认 cache/models/
        """
        self.model_dir = Path(model_dir) if model_dir else MODEL_DIR
        self.model_dir.mkdir(parents=True, exist_ok=True)

    # ============================================================
    #  公开训练接口
    # ============================================================

    def train_xgb_ranker(
        self,
        X_train: Any,
        y_train: Any,
        group_train: Any,
        X_val: Optional[Any] = None,
        y_val: Optional[Any] = None,
        group_val: Optional[Any] = None,
        search_params: bool = False,
    ) -> xgb.Booster:
        """训练 XGBRanker（5 日截面排序模型）

        Parameters
        ----------
        X_train : array-like
            训练特征
        y_train : array-like
            训练标签（超额收益）
        group_train : array-like
            每组（交易日）的样本数列表，如 [3, 5, 2] 表示三个交易日各有 3/5/2 只股票。
            MLDataPipeline.feature_target_split() 返回的是逐样本 qid 标签，
            需用 `np.bincount(qid_labels)` 转换为此格式。
        X_val, y_val, group_val : array-like, optional
            验证集，用于 early stopping
        search_params : bool
            是否先执行网格搜索

        Returns
        -------
        xgb.Booster
            训练完成的 XGBoost 模型
        """
        params: Dict[str, Any] = {**DEFAULT_XGB_PARAMS}

        if search_params:
            best_params = self._grid_search_xgb(X_train, y_train, group_train)
            params.update(best_params)

        dtrain = xgb.DMatrix(X_train, label=y_train)
        dtrain.set_group(group_train)

        evals: List[Tuple[xgb.DMatrix, str]] = [(dtrain, "train")]
        if X_val is not None and group_val is not None and y_val is not None:
            dval = xgb.DMatrix(X_val, label=y_val)
            dval.set_group(group_val)
            evals.append((dval, "val"))

        model = xgb.train(
            params,
            dtrain,
            num_boost_round=200,
            evals=evals,
            early_stopping_rounds=20,
            verbose_eval=False,
        )
        return model

    def train_lgbm_ranker(
        self,
        X_train: Any,
        y_train: Any,
        group_train: Any,
        X_val: Optional[Any] = None,
        y_val: Optional[Any] = None,
        group_val: Optional[Any] = None,
        search_params: bool = False,
    ) -> lgb.Booster:
        """训练 LGBMRanker（20 日截面排序模型）

        Parameters
        ----------
        X_train : array-like
            训练特征
        y_train : array-like
            训练标签（超额收益）
        group_train : array-like
            每组（交易日）的样本数列表，如 [3, 5, 2] 表示三个交易日各有 3/5/2 只股票。
            MLDataPipeline.feature_target_split() 返回的是逐样本 qid 标签，
            需用 `np.bincount(qid_labels)` 转换为此格式。
        X_val, y_val, group_val : array-like, optional
            验证集，用于 early stopping
        search_params : bool
            是否先执行网格搜索

        Returns
        -------
        lgb.Booster
            训练完成的 LightGBM 模型
        """
        params: Dict[str, Any] = {
            **DEFAULT_LGBM_PARAMS,
            # §4.2 P1 修复 (2026-06-08, lib-incremental-audit §1.1):
            # 旧 label_gain = list(range(200)) 线性 [0,1,...,199]
            # 决策: 保留线性 (lambda_rank 文档推荐, 索引 = rank), 改用 [float(i) for i in range(MAX_LABEL_GAIN)]
            #       维持兼容性 + 加注释说明意图
            # 如需强化头部高 label 权重, 改 [2**(i/10) for i in range(MAX_LABEL_GAIN)] 即可
            "label_gain": [float(i) for i in range(MAX_LABEL_GAIN)],  # 线性, 兼容 lambda_rank
        }

        # §4.2 P0 #4 修复 (lib-incremental-audit §2.2):
        # 旧实现直接传连续浮点 label (excess_5d/10d/20d ∈ [-10%, +10%]) 给 LGBM lambdarank
        # LightGBM 内部静默 cast float → int: 负收益 → 0, 0.05 → 0, 0.5 → 0
        # 所有正收益被截断到 0/1 二元 → ranker 退化为分类
        # 修复: pd.qcut(y_train, q=200, labels=False, duplicates='drop') 转离散 rank
        # 优先用 qcut (200 档离散), 失败时降级为 rankdata
        import pandas as _pd
        y_train_arr = _pd.Series(y_train).reset_index(drop=True)
        n_unique = y_train_arr.nunique(dropna=True)
        if n_unique < 2:
            logger.warning(f"[train_lgbm_ranker] y_train unique={n_unique}, LGBM 训练将失败")
        try:
            n_bins = min(MAX_LABEL_GAIN, max(2, n_unique))
            y_train_discrete = _pd.qcut(
                y_train_arr, q=n_bins, labels=False, duplicates="drop"
            ).astype(int).fillna(0).values
        except (ValueError, TypeError) as e:
            logger.warning(f"[train_lgbm_ranker] qcut 失败 ({e}), 降级 rankdata")
            from scipy.stats import rankdata as _rankdata
            y_train_discrete = _rankdata(y_train_arr.fillna(y_train_arr.median()).values) - 1
            y_train_discrete = (y_train_discrete * (MAX_LABEL_GAIN - 1) // max(y_train_discrete.max(), 1)).astype(int)
        y_train = y_train_discrete

        if search_params:
            best_params = self._grid_search_lgbm(X_train, y_train, group_train)
            params.update(best_params)

        train_data = lgb.Dataset(X_train, label=y_train, group=group_train)
        valid_sets: List[lgb.Dataset] = [train_data]
        valid_names: List[str] = ["train"]
        if X_val is not None and group_val is not None and y_val is not None:
            val_data = lgb.Dataset(X_val, label=y_val, group=group_val)
            valid_sets.append(val_data)
            valid_names.append("val")

        model = lgb.train(
            params,
            train_data,
            num_boost_round=200,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
        )
        return model

    # ============================================================
    #  模型序列化
    # ============================================================

    def save_model(self, model: Any, name: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """保存模型并写入 manifest 元信息

        每个模型名称拥有独立的版本序列：
          {model_dir}/{name}/v1/{name}.model + manifest.json
          {model_dir}/{name}/v2/...
          {model_dir}/{name}/current -> v2（符号链接或目录复制）

        Parameters
        ----------
        model : xgb.Booster | lgb.Booster
            待保存的模型对象
        name : str
            模型名称（如 "xgb_5d"），用作子目录名
        metadata : dict
            需持久化的元信息，至少应包含 feature_cols、val_ic、n_samples、n_dates

        Returns
        -------
        dict
            完整 manifest
        """
        model_root = self.model_dir / name
        model_root.mkdir(parents=True, exist_ok=True)

        version = self._next_version(model_root)
        version_dir = model_root / f"v{version}"
        version_dir.mkdir(parents=True, exist_ok=True)

        model_path = version_dir / f"{name}.model"
        try:
            if isinstance(model, xgb.Booster):
                model.save_model(str(model_path))
            elif isinstance(model, lgb.Booster):
                model.save_model(str(model_path))
            else:
                raise TypeError(f"不支持的模型类型: {type(model).__name__}")
        except Exception as e:
            logger.error(f"模型保存失败 {model_path}: {e}")
            raise

        model_type = "xgboost" if isinstance(model, xgb.Booster) else "lightgbm"
        manifest: Dict[str, Any] = {
            "model_name": name,
            "model_type": model_type,
            "version": version,
            "train_date": datetime.now().isoformat(),
            # P0 #15 修复 (2026-06-08, lib-incremental-audit §1):
            # 加 train_data_end_date 字段 (训练数据最后日期)
            # ml_predictor.is_model_stale 用此字段做 stale 检查 (不再用 wall-clock train_date)
            "train_data_end_date": metadata.get("train_data_end_date", datetime.now().date().isoformat()),
            "feature_cols": metadata.get("feature_cols", []),
            "val_ic": metadata.get("val_ic", None),
            "n_samples": metadata.get("n_samples", 0),
            "n_dates": metadata.get("n_dates", 0),
        }

        manifest_path = version_dir / "manifest.json"
        try:
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"manifest 写入失败 {manifest_path}: {e}")
            raise

        # 更新 current 链接（Windows 兜底用目录复制）
        self._update_current_link(version_dir, model_root)

        logger.info(f"模型已保存: {name} v{version} -> {version_dir}")

    @staticmethod
    def qid_to_group(qid_labels: np.ndarray) -> np.ndarray:
        """将逐样本 qid 标签转换为 XGBRanker/LGBMRanker 所需的 group sizes

        Parameters
        ----------
        qid_labels : np.ndarray
            MLDataPipeline.feature_target_split() 返回的逐样本组标签

        Returns
        -------
        np.ndarray
            每组大小数组，如 [3, 5, 2]
        """
        return np.bincount(qid_labels)

    def load_latest(self, name: str) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
        """加载指定名称的最新版本模型 + manifest

        Parameters
        ----------
        name : str
            模型名称（如 "xgb_5d"），用作子目录名

        Returns
        -------
        tuple[model | None, manifest | None]
            (模型对象, manifest 字典)，不存在时返回 (None, None)
        """
        model_root = self.model_dir / name
        if not model_root.exists():
            logger.warning(f"模型目录不存在: {model_root}")
            return None, None

        current_link = model_root / "current"
        version_dir = self._resolve_current(current_link)
        if version_dir is None:
            logger.warning(f"未找到 current 链接: {current_link}")
            return None, None

        model_path = version_dir / f"{name}.model"
        if not model_path.exists():
            logger.warning(f"模型文件不存在: {model_path}")
            return None, None

        manifest: Optional[Dict[str, Any]] = None
        manifest_path = version_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"manifest 读取失败 {manifest_path}: {e}")

        try:
            model_type = manifest.get("model_type", "") if manifest else ""
            # §4.2 P1-3 修复 (2026-06-08, lib-incremental-audit §1.1):
            # 旧实现 "name.lower()" 启发式 — manifest 缺 model_type 时按名称猜
            # 风险: lgbm_5d/ 目录 model_type 缺失 → 走 XGB 路径 → silent corruption
            # 修复: model_type 缺失时 raise ValueError, 不再启发式
            if not model_type:
                raise ValueError(
                    f"manifest {manifest_path} 缺 model_type 字段 — "
                    f"无法确定加载 XGB 还是 LGBM, 拒绝启发式"
                )
            if model_type == "xgboost":
                model = xgb.Booster()
                model.load_model(str(model_path))
            elif model_type == "lightgbm":
                model = lgb.Booster(model_file=str(model_path))
            else:
                raise ValueError(f"未知模型类型: {model_type}（仅支持 xgboost/lightgbm）")
        except Exception as e:
            logger.error(f"模型加载失败 {model_path}: {e}")
            return None, manifest

        logger.info(
            f"模型已加载: {name} v{manifest.get('version', '?') if manifest else '?'}"
        )
        return model, manifest

    # ============================================================
    #  网格搜索
    # ============================================================

    def _grid_search_xgb(
        self,
        X: Any,
        y: Any,
        group: Any,
        param_grid: Optional[Dict[str, List[Any]]] = None,
    ) -> Dict[str, Any]:
        """XGBoost 简易网格搜索（3 折 CV，best ndcg@5）

        Parameters
        ----------
        X, y, group : array-like
            训练数据
        param_grid : dict, optional
            搜索空间，默认 {max_depth:[4,6,8], learning_rate:[0.05,0.1]}

        Returns
        -------
        dict
            最佳参数子集
        """
        if param_grid is None:
            param_grid = {"max_depth": [4, 6, 8], "learning_rate": [0.05, 0.1]}

        base_params: Dict[str, Any] = {**DEFAULT_XGB_PARAMS}

        best_score = -np.inf
        best_params: Dict[str, Any] = {}

        dtrain = xgb.DMatrix(X, label=y)
        dtrain.set_group(group)

        for params in ParameterGrid(param_grid):
            merged = {**base_params, **params}
            try:
                # P0-5: TimeSeriesSplit + embargo（之前 nfold=3 是随机 K-Fold，导致标签泄漏）
                cv_result = xgb.cv(
                    merged,
                    dtrain,
                    num_boost_round=100,
                    folds=_TimeSeriesSplitWithEmbargo(n_splits=3, embargo_gap=5),
                    early_stopping_rounds=10,
                    verbose_eval=False,
                )
                score = float(cv_result["test-ndcg@5-mean"].max())
            except Exception as e:
                logger.warning(f"XGB CV 失败 params={params}: {e}")
                continue

            if score > best_score:
                best_score = score
                best_params = dict(params)

        logger.info(f"XGB 最佳参数: {best_params}, ndcg={best_score:.4f}")
        return best_params

    def _grid_search_lgbm(
        self,
        X: Any,
        y: Any,
        group: Any,
        param_grid: Optional[Dict[str, List[Any]]] = None,
    ) -> Dict[str, Any]:
        """LightGBM 简易网格搜索（3 折 CV，best ndcg@5）

        Parameters
        ----------
        X, y, group : array-like
            训练数据
        param_grid : dict, optional
            搜索空间，默认 {num_leaves:[15,31,63], learning_rate:[0.05,0.1]}

        Returns
        -------
        dict
            最佳参数子集
        """
        if param_grid is None:
            param_grid = {"num_leaves": [15, 31, 63], "learning_rate": [0.05, 0.1]}

        base_params: Dict[str, Any] = {
            **DEFAULT_LGBM_PARAMS,
            "label_gain": list(range(MAX_LABEL_GAIN)),
        }

        best_score = -np.inf
        best_params: Dict[str, Any] = {}

        train_data = lgb.Dataset(X, label=y, group=group)

        for params in ParameterGrid(param_grid):
            merged = {**base_params, **params}
            try:
                # P0-5: TimeSeriesSplit + embargo（之前 nfold=3 是随机 K-Fold）
                cv_result = lgb.cv(
                    merged,
                    train_data,
                    num_boost_round=100,
                    folds=_TimeSeriesSplitWithEmbargo(n_splits=3, embargo_gap=5),
                    callbacks=[lgb.early_stopping(10), lgb.log_evaluation(0)],
                )
                # 查找 ndcg 相关的 mean 列（兼容不同 eval_at 配置）
                ndcg_key = self._find_ndcg_column(list(cv_result.keys()))
                if ndcg_key is None:
                    logger.warning(f"LGBM CV 结果中未找到 ndcg 列: {list(cv_result.keys())}")
                    continue
                score = float(max(cv_result[ndcg_key]))
            except Exception as e:
                logger.warning(f"LGBM CV 失败 params={params}: {e}")
                continue

            if score > best_score:
                best_score = score
                best_params = dict(params)

        logger.info(f"LGBM 最佳参数: {best_params}, ndcg={best_score:.4f}")
        return best_params

    # ============================================================
    #  内部工具方法
    # ============================================================

    def _next_version(self, model_root: Path) -> int:
        """计算模型名下个版本号"""
        if not model_root.exists():
            return 1
        existing = [
            d
            for d in model_root.iterdir()
            if d.is_dir() and d.name.startswith("v")
        ]
        if not existing:
            return 1
        versions = []
        for d in existing:
            try:
                versions.append(int(d.name[1:]))
            except ValueError:
                continue
        return max(versions) + 1 if versions else 1

    def _update_current_link(self, version_dir: Path, model_root: Path) -> None:
        """更新 current 符号链接指向 version_dir

        Windows 上可能缺少符号链接权限，兜底用目录复制。
        """
        current_link = model_root / "current"
        # 清除旧的 current
        if current_link.is_symlink() or current_link.exists():
            try:
                if current_link.is_dir() and not current_link.is_symlink():
                    shutil.rmtree(str(current_link))
                else:
                    current_link.unlink()
            except Exception as e:
                logger.warning(f"清除旧 current 失败: {e}")

        # 优先符号链接
        try:
            current_link.symlink_to(version_dir.name, target_is_directory=True)
            return
        except (OSError, NotImplementedError):
            pass

        # 兜底：复制目录
        try:
            if current_link.exists():
                shutil.rmtree(str(current_link))
            shutil.copytree(str(version_dir), str(current_link))
            logger.info("使用目录复制替代符号链接（current）")
        except Exception as e:
            logger.warning(f"current 目录复制失败: {e}")

    def _resolve_current(self, current_link: Path) -> Optional[Path]:
        """解析 current 链接/目录指向的实际版本目录"""
        if not current_link.exists():
            return None

        # 符号链接
        if current_link.is_symlink():
            target = current_link.resolve()
            if target.exists():
                return target
            return None

        # 普通目录（Windows 兜底）
        if current_link.is_dir():
            return current_link

        return None

    @staticmethod
    def _find_ndcg_column(keys: List[str]) -> Optional[str]:
        """在 LGBM CV 结果键中查找 ndcg 的 mean 列

        LGBM 4.x CV 返回列名格式为 "valid ndcg@5-mean"。
        优先选择 ndcg@5-mean，降级到通用 ndcg-mean。
        """
        candidates = []
        for k in keys:
            lower = k.lower().replace("valid ", "")
            if lower.endswith("-mean") and "ndcg" in lower:
                candidates.append((k, lower))
        # 优先 @5, @10, @1
        for suffix in ("@5", "@10", "@1"):
            for raw, clean in candidates:
                if suffix in clean:
                    return raw
        # 降级任意
        if candidates:
            return candidates[0][0]
        return None
