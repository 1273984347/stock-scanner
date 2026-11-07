"""未接线的回测能力 —— 保留不删。

本目录下的模块**已实现但未接线**（全仓零 import 引用），它们不是废弃代码，
而是 2026-05~06 已交付但尚未接入主回测流程的能力。

保留原因：删除等于销毁已完成的工作；重新实现则必然产生行为漂移。

当前状态（2026-11-07 实测）：
- `benchmark.py`（149 行）：`get_benchmark_returns` / `compute_benchmark_metrics`
  / `compare_with_benchmarks` / `format_benchmark_comparison` + `BenchmarkResult`
- `monte_carlo.py`（298 行）：`run_monte_carlo` / `run_bootstrap`
  / `run_cost_sensitivity` + 3 个结果类

处置口径：照 RF-01 Phase 3 原定方案「移入 legacy 并声明未接线、保留不删」。

⚠️ 注意：`backtest/stress.py` 内另有 `_run_monte_carlo`（私有），
与本目录的 `run_monte_carlo`（公开）是**两份不同实现**，不是重复 ——
前者嵌在压力测试流程里，后者是独立的蒙特卡洛/自助法/成本敏感性工具集。
接线前需先对拍两者的数值口径是否一致（见 ADR-0002 的同类判据）。
"""
