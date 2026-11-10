"""统一评分契约 —— RF-01 Phase 4 的「一个内核」。

**归一的是契约，不是算法**（见 `docs/adr/0003-*.md`）：
实测 7 个评分模块 / 9 个入口的语义互不相同（四维加权 / 四层打分卡 /
51 评委投票 / 4 风格 Agent / 投票决策 / V7 机会分 / 策略层 quick·full），
不存在可合并的重复实现。强行合并会静默改变评分数值，违反 `RULES.md §2.3 B1/B2`。

因此本模块只定义三件事，且**不做任何数值计算**：

1. `ScoringResult` —— 统一结果形状（6 个冻结字段 + `raw` 原样保留）。
2. `AdapterSpec` / `ScoringKernel` —— 唯一分发点；适配器只做「一次调用 + 只读提取」。
3. `PRIMARY_4D_SOURCE` —— 四维总分的**唯一真源**声明。
   评委团 / Agent / 机会分等是各自量纲的**附加维度**，绝不参与四维混算。

硬约束（由 `tests/features/test_scoring_contract.py` 用 AST 自动守卫）：
- `raw` 必须是旧实现返回值的**同一对象引用**（类比 `backtest/unified.py` 的 B4）。
- 适配器函数体不得出现算术运算（`BinOp`）—— 权重计算只能发生在旧实现里。
- 消费者是否切到内核由 `config.unified_scoring_enabled(consumer)` 决定，默认 off。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

# 四维总分（fundamental / capital_flow / news / technical）的唯一权威来源。
# 其它适配器返回的 total 属其自身量纲，不得与此混算。
PRIMARY_4D_SOURCE = "scorer_4d"

FOUR_DIMENSIONS: Tuple[str, ...] = (
    "fundamental", "capital_flow", "news", "technical",
)


def _extract(raw: Any, key: str) -> Any:
    """只读提取：dict 取键 / 对象取属性 / 其余返回 None。

    这是内核唯一允许的「加工」，没有任何算术或默认值填充。
    """
    if raw is None or not key:
        return None
    if isinstance(raw, Mapping):
        return raw.get(key)
    return getattr(raw, key, None)


@dataclass(frozen=True)
class ScoringResult:
    """统一评分结果。

    Attributes:
        source: 适配器名（对应 `AdapterSpec.name`）。
        dimensions: 旧实现自带的分项，原样收集，不重算、不补齐。
        total: 该实现自己的主分（量纲由该实现定义，彼此不可比）。
        level: 该实现自己的评级。
        raw: **旧实现返回值的同一对象引用** —— 对拍基线的取证依据。
        authoritative_4d: 是否四维总分真源；只有它为 True 时 `to_dict()`
            才填充四个维度字段。
    """

    source: str
    dimensions: Mapping[str, Any]
    total: Optional[float]
    level: Optional[str]
    raw: Any
    authoritative_4d: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """冻结的对外形状：`{fundamental, capital_flow, news, technical, total, level}`。

        非真源的四个维度恒为 None —— 这正是「附加维度不与四维分混算」的机器表达。
        """
        out = {k: None for k in FOUR_DIMENSIONS}
        out["total"] = self.total
        out["level"] = self.level
        if self.authoritative_4d:
            for k in FOUR_DIMENSIONS:
                out[k] = self.dimensions.get(k)
        return out


@dataclass(frozen=True)
class AdapterSpec:
    """适配器声明。

    `call` 必须是**纯转发**：`return 旧实现(**inputs)`，不做任何计算。
    `total_key` / `level_key` / `dimension_keys` 只描述从哪里**读**，不改写值。
    """

    name: str
    call: Callable[[Mapping[str, Any]], Any]
    total_key: str = "score"
    level_key: str = "rating"
    dimension_keys: Tuple[str, ...] = ("breakdown",)
    authoritative_4d: bool = False


class ScoringKernel:
    """唯一分发点：按 source 找到适配器，调用一次，包装成统一结果。

    内核不缓存、不改输入、不重算。`score()` 始终可用（供对拍与特征测试），
    但**生产消费者是否启用**由 `config.unified_scoring_enabled(consumer)` 控制，
    默认全关 ⇒ 阶段 A 引入内核不会改变任何端点响应。
    """

    def __init__(self) -> None:
        self._registry: Dict[str, AdapterSpec] = {}

    def register(self, spec: AdapterSpec) -> None:
        if not spec.name:
            raise ValueError("AdapterSpec.name must not be empty")
        if spec.name in self._registry:
            raise ValueError(f"duplicate scoring adapter: {spec.name}")
        if spec.authoritative_4d and spec.name != PRIMARY_4D_SOURCE:
            raise ValueError(
                f"only {PRIMARY_4D_SOURCE!r} may be authoritative_4d, got {spec.name!r}"
            )
        self._registry[spec.name] = spec

    @property
    def sources(self) -> Tuple[str, ...]:
        return tuple(sorted(self._registry))

    def spec(self, name: str) -> AdapterSpec:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise KeyError(
                f"unknown scoring source {name!r}; registered: {', '.join(self.sources)}"
            ) from exc

    def score(self, source: str, inputs: Optional[Mapping[str, Any]] = None) -> ScoringResult:
        """调用一次旧实现，包装为 `ScoringResult`。数值零变更。"""
        spec = self.spec(source)
        payload = {} if inputs is None else inputs
        raw = spec.call(payload)  # ← 唯一的调用点，适配器之外没有任何加工

        dimensions: Dict[str, Any] = {}
        for key in spec.dimension_keys:
            value = _extract(raw, key)
            if isinstance(value, Mapping):
                dimensions.update(value)
            elif value is not None:
                dimensions[key] = value

        return ScoringResult(
            source=spec.name,
            dimensions=dimensions,
            total=_extract(raw, spec.total_key),
            level=_extract(raw, spec.level_key),
            raw=raw,
            authoritative_4d=spec.authoritative_4d,
        )


def unified_scoring_enabled(consumer: str = "default") -> bool:
    """按消费者粒度判定开关（默认 off）。转调 config，避免在此复制判定逻辑。"""
    from config import unified_scoring_enabled as _enabled
    return _enabled(consumer)
