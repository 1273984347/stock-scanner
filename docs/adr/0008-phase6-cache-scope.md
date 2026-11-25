---
name: ADR-0008-phase6-cache-scope
description: Phase 6 第 3 批 —— 不新增回测端点 TTL 缓存；实测既有 /api/scan 缓存已满足计划要求，回测端点缓存会改变响应语义，范围正式裁剪
metadata:
  type: decision
---

# ADR-0008: Phase 6 第 3 批 · 慢端点缓存范围裁剪

**Status:** Accepted
**Date:** 2026-11-11
**Deciders:** 自己
**相关:** `docs/plans/2026-11-07-refactor-plan.md` §三 Phase 6；
`docs/adr/0006-phase6-blocking-io-criteria.md`；`docs/adr/0007-phase6-offload-and-locks.md`；
`lib/infra/api_core.py::_cache_get/_cache_set`；`routers/scan.py`；`TODO.md` Phase 6 台账

---

## Context

计划 §三 Phase 6「范围（做）」第 3 点写：**慢端点加缓存层（带 TTL 与失效策略）**。
第 2 批结束时（`8c0342c`）L8 读数：68 端点 / **228 阻塞调用** / 60 个端点含阻塞点 /
4 个已 offload。按字面执行第 3 点，就要给 `/api/backtest` 等端点新增缓存。

上一批结论里已给出建议「TTL 缓存直接砍掉」，本 ADR 把该建议变成**有证据的范围决策**，
而不是静默不做。

### 实测：缓存层已经存在

| 证据 | 位置 |
|---|---|
| `/api/scan` 读缓存 `max_age=3600`，未命中才启动后台扫描 | `routers/scan.py:14-17` |
| `_cache_get` 有 **±10% 随机 TTL 抖动**（防缓存雪崩）、过期自动 `unlink` | `lib/infra/api_core.py:119-144` |
| 读缓存全程 `_cache_lock` 保护，gzip 透明解压、向后兼容未压缩文件 | 同上 |
| `_cache_set` 有压缩、原子写与 Windows 文件权限收紧 | `lib/infra/api_core.py:147-165` |
| 后台扫描完成后回写同一 key | `routers/common.py::_run_scan_background` |

即：**「带 TTL 与失效策略的缓存层」不是待办，是本阶段之前就已实现的能力**，
且抖动雪崩防护比计划要求的更细。这是本项目**第 7 次**计划清单里的假设被实测推翻
（前 6 次：8 套评分实现 / 60% 覆盖率 / 端点切点 / 域外端点 / 18 条前端路径 /
阻塞调用 ≤1）。

### 为什么不给回测端点新增缓存

1. **命中率无数据支持**：参数空间 = `symbols` 组合 × `days` × 6 个风控/阈值参数 × `preset`，
   实际几乎不会重复；缓存表会膨胀而命中趋近 0。
2. **改变响应语义**：回测结果依赖行情数据与模型版本。缓存命中会让「同一请求」返回
   过去时刻的结果，与 Phase 6 兼容策略「**不改任何响应内容**」直接冲突，
   也会让 L4 回测口径基线失去意义。
3. **失效键无法可靠定义**：数据源更新、TDX 日线更新、模型换版、参数变化都必须失效；
   漏掉任何一项就是**静默返回陈旧结果**，比慢一点危险得多。
4. **收益不抵复杂度**：真正解决阻塞的是第 2 批的 offload（已在 L9 取证），
   缓存是正交优化，不属于「消除事件循环阻塞」的关键路径。

---

## Decision

### D1 · 不新增任何端点缓存

- 不引入 `FEATURE_BACKTEST_CACHE`，不改 `/api/backtest`、`/api/backtest/compare`、
  `/api/walkforward` 的缓存行为；
- 计划 §三 Phase 6 第 3 点标注为「**已由既有 `/api/scan` 缓存满足；回测端点经评估不做**」；
- `TODO.md` 第 3 批该项按 C6 标 `[~]` 并注明移除日期与原因（不是 `[x]`，因为没做）。

### D2 · 既有缓存纳入 Phase 6 验收证据，而非忽略

`/api/scan` 的 TTL + 抖动 + 锁 + 失效路径由机器判据冻结
（`tests/test_phase6_batch3.py`）：端点必须调 `_cache_get`、`max_age` 必须为 3600、
`_cache_get` 必须含 jitter 与 `_cache_lock`。
这样「已存在」是可复算的结论，不是口头声明。

### D3 · 备选与代价（若将来要重启）

若后续有真实调用重复率数据支持，做法应为：
`FEATURE_BACKTEST_CACHE` **默认 off**、缓存键含
`symbols + 全部参数摘要 + 数据源版本 + 模型版本`、
显式失效钩子、单独 ADR、off/on 整份响应与「陈旧性」专项取证。
代价：一个新开关、一套失效机制、一份新基线与长期维护面；
在命中率数据出来之前，这笔交易不划算。

---

## Consequences

- **正面**：Phase 6 范围收敛到「消除事件循环阻塞」这一件事；
  不引入响应新鲜度语义，L4 回测基线与「不改响应」承诺继续成立。
- **代价**：慢端点在**重复相同参数**时不会有加速；若线上确实存在大量重复回测请求，
  需另起一个带数据的优化议题，不混在重构阶段里。
- **纪律**：判据/范围被实测推翻时，走「实测证据 → 改范围 → 留 ADR → 补机器判据 → 台账标 `[~]`」，
  不是把待办悄悄划掉，也不是为了对齐计划去造一个收益存疑的功能。
- **落地载体**：本 ADR；计划 §三 Phase 6 范围（做）第 3 点与 §七第 3 批记录；
  `TODO.md` Phase 6 第 3 批台账；`tests/test_phase6_batch3.py`；
  `RULES.md` / `docs/refactor/README.md` / `CLAUDE.md` 的相应状态行。
