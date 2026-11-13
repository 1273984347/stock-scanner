# RF-02 执行计划 · 3 坏端点复原 + dashboard 异步治理 + judges.json 漂移收口

> 立项：2026-11-13（ADR-0015 **Accepted**）。RF-01 已收官（`36edc86`，Phase 0–7）。
> 本计划是 RF-01 之后的下一个独立项目，退出判据见 `docs/adr/0015-rf02-scope-and-fixes.md`。
> 纪律沿用 RF-01：① 先 ADR 定方案；② 每批改完跑 L0–L10 + 契约快照 + 回归；③ 六文档同步；
> ④ 本地提交不推送，待用户确认。

---

## 范围（三块，见 ADR-0015 Facts）

| 块 | 现状（已核对） | RF-02 处置 |
|---|---|---|
| 坏端点 notifications×2 | 调不存在的 `get_notifications` + 按 message/details 检索（字段不存在）→ 永远空兜底 | D1：补模块级 `get_notifications` + 检索改 title/body |
| 坏端点 strategy/recommend | 导入不存在的 `FactorOptimizer` + 调用不存在的 `_default_weights/evaluate_weights` → 永远 error 兜底 | D2：改调 `eval_current_weights` |
| judges.json 漂移 | 文件把 6 个被 P0#13 禁用的评委组重新加权启用，抵消代码修复 | D3：删除文件（首选）或对齐默认 |
| /api/dashboard 异步治理 | 重扫描已 `asyncio.to_thread`，缓存未命中 4 个阻塞调用仍在事件循环 + 含 await 不可逐字搬 | D4-a：抽 `_compute_dashboard_market` 经 `run_blocking`（推荐）/ D4-b：补快照+监测 |

---

## 分批执行

### RF-02-1 · 3 坏端点复原（D1 + D2）
- 改 `lib/monitoring/notification_logger.py`：加模块级 `get_notifications(limit=…)`。
- 改 `routers/notifications.py`：关键词检索 message/details → title/body。
- 改 `routers/strategy.py::_compute_strategy_recommend`：去 `FactorOptimizer`，改调 `eval_current_weights`。
- 契约快照：对两对端点做「修复前（空/error 兜底）↔ 修复后（真实数据）」基线对照，证明只复原不改口径。
- 门禁：py_compile + pytest + 契约快照 diff=0（字段顺序一致）；新增/修正针对性回归测试。
- 文档：ADR-0015 记 D1/D2 实测读数；pitfalls 新增（如有新坑）；TODO 回勾。

### RF-02-2 · judges.json 漂移收口（D3，待用户确认删文件）
- 删除 `config/judges.json`（首选）→ `judge_panel` 回退 `_DEFAULT_JUDGE_GROUPS`（6 组 weight=0、AI量化=1.00）。
- 加一致性测试：断言 `JUDGE_GROUPS` 中 6 个组权重为 0、AI量化=1.00（防 judges.json 被悄悄加回）。
- 文档：ADR-0015 D3 读数；CLAUDE/README 若提及 judges.json 覆盖机制需同步。

### RF-02-3 · dashboard 异步治理（D4，待用户确认方案）
- D4-a（推荐）：抽 `_compute_dashboard_market(...)` 经 `run_blocking`；保留含 await 的 `run_all_strategies` 不动。
- D4-b（最小）：补契约快照 + 运行期阻塞耗时监测，不重构。
- 门禁：L8（dashboard 的 blocking 计数变化需重登 tier-b 或豁免登记）、L10（dashboard 纳入并发探针）、契约快照。

### RF-02-4 · 收口与经验沉淀
- 六文档同步（RULES / README / TODO / CLAUDE / 本计划 / pitfalls）。
- ADR-0015 状态 Accepted。
- 本地提交（不推送）；CI 三平台仍卡在 GitHub Billing，需用户解卡后复跑（与 RF-01 退出判据 ③ 同卡点）。

---

## 风险与纪律

- **行为变更必须契约快照**：D1/D2 改变响应内容，没有快照对照就改 = 无证据。
- **删文件前确认**（D3）：外部可见改动，用户拍板。
- **动含 await 端点前确认**（D4-a）：避免线程池套娃；off 路径同步直调即可。
- **不把修复顺手扩成重构**：只复原意图，不引入新字段/新能力。
- **不推送**：master 已 ahead 多 commit；推送等用户确认；CI 卡点在用户账户 Billing。
