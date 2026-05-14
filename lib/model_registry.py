"""ML 模型版本注册表 —— 模型版本管理（保存/加载/回滚/列表）

独立于 MLTrainer，提供更干净的模型生命周期管理接口。

依赖: xgboost, lightgbm
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import lightgbm as lgb
import xgboost as xgb

logger = logging.getLogger(__name__)

DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "cache" / "models"


class ModelRegistry:
    """模型版本注册表

    管理模型的多版本存储、加载、回滚。
    每个模型名称拥有独立的版本序列：
      {model_dir}/{name}/v{version}/{name}.model + manifest.json
      {model_dir}/{name}/current -> v{version}（符号链接或目录复制）

    Parameters
    ----------
    model_dir : str, optional
        模型存储根目录，默认 cache/models/
    """

    def __init__(self, model_dir: str = "cache/models"):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

    # ============================================================
    #  公开接口
    # ============================================================

    def save(self, model: Any, name: str, metadata: Dict[str, Any]) -> str:
        """保存模型并写入 manifest 元信息

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
        str
            版本目录的绝对路径
        """
        model_root = self.model_dir / name
        model_root.mkdir(parents=True, exist_ok=True)

        version = self._next_version(model_root)
        version_dir = model_root / f"v{version}"
        version_dir.mkdir(parents=True, exist_ok=True)

        # 保存模型文件
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

        # 写入 manifest
        model_type = "xgboost" if isinstance(model, xgb.Booster) else "lightgbm"
        manifest: Dict[str, Any] = {
            "model_name": name,
            "model_type": model_type,
            "version": version,
            "train_date": datetime.now().isoformat(),
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

        # 更新 current 链接
        self._update_current_link(version_dir, model_root)

        logger.info(f"模型已保存: {name} v{version} -> {version_dir}")
        return str(version_dir)

    def load(self, name: str) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
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

        # 读取 manifest
        manifest: Optional[Dict[str, Any]] = None
        manifest_path = version_dir / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"manifest 读取失败 {manifest_path}: {e}")

        # 加载模型（依据 model_type）
        try:
            model_type = (manifest or {}).get("model_type", "")
            if model_type == "xgboost":
                model = xgb.Booster()
                model.load_model(str(model_path))
            elif model_type == "lightgbm":
                model = lgb.Booster(model_file=str(model_path))
            else:
                logger.warning(f"未知模型类型: {model_type}，尝试 LGBM")
                model = lgb.Booster(model_file=str(model_path))
        except Exception as e:
            logger.error(f"模型加载失败 {model_path}: {e}")
            return None, manifest

        version_str = str(manifest.get("version", "?")) if manifest else "?"
        logger.info(f"模型已加载: {name} v{version_str}")
        return model, manifest

    def rollback(self, name: str) -> None:
        """回滚到上一个版本

        将 current 链接指向前一个版本目录。
        如果不存在前一个版本或模型目录缺失，不做任何操作。

        Parameters
        ----------
        name : str
            模型名称
        """
        model_root = self.model_dir / name
        if not model_root.exists():
            logger.warning(f"回滚失败: 模型目录不存在 {model_root}")
            return

        current_link = model_root / "current"
        current_dir = self._resolve_current(current_link)
        if current_dir is None:
            logger.warning(f"回滚失败: '{name}' 无 current 版本")
            return

        # 提取当前版本号
        try:
            current_version = int(current_dir.name[1:])  # "v3" -> 3
        except (ValueError, IndexError):
            logger.warning(f"回滚失败: 版本目录名无效 {current_dir.name}")
            return

        # 找到上一个版本
        versions = self._list_version_numbers(model_root)
        versions.sort()
        prev_version: Optional[int] = None
        for v in reversed(versions):
            if v < current_version:
                prev_version = v
                break

        if prev_version is None:
            logger.warning(f"回滚失败: '{name}' 无更早的版本 (当前 v{current_version})")
            return

        prev_dir = model_root / f"v{prev_version}"
        self._update_current_link(prev_dir, model_root)
        logger.info(f"模型已回滚: {name} v{current_version} -> v{prev_version}")

    def list_versions(self, name: str) -> List[Dict[str, Any]]:
        """列出指定模型的所有版本

        Parameters
        ----------
        name : str
            模型名称

        Returns
        -------
        list[dict]
            每个版本 manifest 的列表，按版本号降序排列
        """
        model_root = self.model_dir / name
        if not model_root.exists():
            logger.warning(f"模型目录不存在: {model_root}")
            return []

        versions = self._list_version_numbers(model_root)
        versions.sort(reverse=True)

        result: List[Dict[str, Any]] = []
        for v in versions:
            version_dir = model_root / f"v{v}"
            manifest_path = version_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    result.append(manifest)
                except Exception as e:
                    logger.warning(f"manifest 读取失败 {manifest_path}: {e}")
                    result.append({"version": v, "model_name": name, "error": str(e)})
            else:
                result.append({"version": v, "model_name": name, "error": "manifest missing"})

        return result

    # ============================================================
    #  内部工具方法
    # ============================================================

    def _next_version(self, model_root: Path) -> int:
        """计算模型名下个版本号"""
        if not model_root.exists():
            return 1
        versions = self._list_version_numbers(model_root)
        return max(versions) + 1 if versions else 1

    def _list_version_numbers(self, model_root: Path) -> List[int]:
        """列出模型目录下的所有版本号"""
        existing = [
            d
            for d in model_root.iterdir()
            if d.is_dir() and d.name.startswith("v")
        ]
        versions = []
        for d in existing:
            try:
                versions.append(int(d.name[1:]))
            except ValueError:
                continue
        return versions

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


# ============================================================
#  快速验证
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    registry = ModelRegistry()
    print(f"ModelRegistry 导入成功")
    print(f"模型目录: {registry.model_dir}")
