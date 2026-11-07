"""性能指标缓冲 —— 从 api.py 抽出的跨端点共享状态（RF-01 Phase 1）

背景：性能指标在多个端点的中间件里累积，满 50 条落盘。
原先是 api.py 的模块级 list，api.py 拆分后无法跨模块累积 ⇒ 属拆分硬阻塞。

本模块持有**唯一**状态对象；api.py 以
`from lib.infra.perf_metrics import _perf_metrics` 引用**同一个对象**（不是拷贝）。
注意：只能**原地**操作（append / clear），不要重新赋值，否则会与本模块分裂。

兼容：`from api import _perf_metrics` 仍然可用。
"""

from typing import List

# 性能指标缓冲（原地 append / clear）
_perf_metrics: List[dict] = []
