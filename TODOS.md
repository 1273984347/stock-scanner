# TODOS

> 任务唯一台账。每次操作后更新；完成项标 `[x]`，当日结束前归入「已完成」，
> 避免重复排期。最近更新：2026-11-27 02:55。

## 进行中

- [ ] blocks 反向缺口 23 只（新股，快照有但从未有映射）——新浪行业未收录，需评估兜底来源
- [ ] fundflow 359 只上游缺口待恢复后重跑

## 待办

- [ ] 公开仓 CI 首周观察：失败按根因修，不通过放宽判据或改基线压红
- [ ] 公开/内部文档边界复盘：评估 `docs/adr`、`docs/plans` 中可逐步公开的部分
- [ ] 复核继承 backlog（见下节），删除过时项后再排期

## 已完成（2026-11-27）

- [x] **blocks 覆盖率判据落地（P-146）**：`fetch_industry_mapping` 返回失败板块清单，
      `sync_blocks` 写前按 `max(上一版文件数, 快照标的数)` 校验，<98% 整批不写 + fail=1；
      `tests/test_blocks_coverage.py` 9 条判据；实跑 5,635/5,635、覆盖 100.0%。
- [x] **blocks 重跑补齐 750 只**：5,635/5,635 落盘、fail=0、113s；复核 stale=0（全仓 mtime 11-27）。
      与重跑前快照比对：5,290 一致 / 345 变化 / 0 新增 / 0 消失 / 0 空值；变化为清掉伪二级标签
      （"金属制品、机械和设备修理业" 194→1，造纸股误挂"黑色金属"等），一级行业计数稳定 ⇒ 质量改善。
      11-26 的缺口确认是**瞬时失败**，非源缺失；覆盖判据仍需补（P-146）。
- [x] **blocks 750 差额核对**（`scripts/audit_blocks_gap.py`，落 `cache/blocks_gap_2026-11-26.json`）：
      0 退市。742 只在当日快照（活标的）+ 8 只 B 股；过期集中在 12 个行业（专用设备 423 只）；
      反向缺口 23 只新股。根因＝12 个板块成分股拉取失败被 debug 吞掉 + 无覆盖判据（坑表 P-146）。
- [x] **清理 `scripts/` 下沉目录**：删除 49 个硬链接副本（同 inode，删名不删数据）+ 误建的
      `scripts/data/exports/`（清单为截断版，真实数据在 `data/exports/bulk/`）+ 一次性
      `restructure_project.ps1`。工作区零未跟踪，回到根级布局；未完成的迁移正式放弃。
- [x] **P0 工作区收尾（4 批提交）**：`af416e9` 忽略 backups；`2b0493b` 因子接入与证伪收口；
      `801d7d9` iFinD 取证脚本 7 个；`9b805b0` 批量取数工具 16 个 + `manifest.json` 入库。
- [x] iFinD 风险因子加权线**关闭**：严格前瞻两个零重叠窗口符号翻转（+39.95pp / −35.90pp，
      两窗平均 +2.03pp）⇒ 判定证伪，`use_ifind_risk_score` 保持 False 且不计划启用；
      ADR-0037 决策4 + 坑表 P-144 + AST 守卫（生产置 True 判红）。不做第三时点、不做反向、不做 regime。

## 已完成（2026-11-26）

- [x] north/south 强制重抓并重建 parquet（20:24；north 9,510 行 / south 275 行）

- [x] `financial` 5,580/5,580 成功；`fundflow` 5,222 只落仓（359 只上游不可用已登记）

- [x] `blocks` 刷新完成：19:23，4,885 只映射落盘（旧 5,635 文件保留待核）

- [x] 建立 `docs/data-freshness-gaps.md`：按类别列出新鲜度、缺口与刷新顺序

- [x] `.gitignore` 新增 `data/exports/` 忽略规则，避免 `git add -A` 误提交 5.9GB 导出数据
- [x] Go CLI 鉴权态真实命令回归：新增 opt-in 测试并本机实跑通过（30.5s）

- [x] 待推提交已全部推送（`8ad904a` → `423b4cb`），CI run `45799831564` 三平台全绿

- [x] 新增鉴权态回归：CI 用 fake CLI 验证解析路径；真实网络用
      `WESTOCK_GO_AUTH_TEST=1 pytest ... -q` opt-in 执行

- [x] SECURITY.md 新增漏洞报告模板（复制填写、脱敏要求）
- [x] Go CLI 未鉴权真实回归：隔离 HOME/APPDATA 实测 rc=1 + AUTH_REQUIRED，
      11 个封装全部降级为空且不抛错
- [x] Go CLI 未鉴权日志脱敏：AUTH_REQUIRED 一次性授权 URL（含 code=）不再写入日志

- [x] 推送 westock + 治理文件两批提交，公开仓 CI 三平台全绿（run `45673551198`）

- [x] 清理 `.env` 中 `FEISHU_*` 四行（飞书应用已删除，密钥失效）
- [x] 提交 westock 六类命令 WIP（`051eb31`，含契约测试）
- [x] 建立 `TODOS.md` / `HANDOVER.md` 实时台账
- [x] 删除 `TODO.md`：28 项未完成项已按主题归并进本文件，`AGENTS.md` / `CLAUDE.md` /
      `RULES.md` 的台账引用同步切到 `TODOS.md`
- [x] 补齐公开仓治理文件：`LICENSE` / `SECURITY.md` / `CHANGELOG.md` / `ROADMAP.md` /
      `CODE_OF_CONDUCT.md` / `CONTRIBUTING.md`
- [x] 公开仓 CI 三平台绿（重建后基线 run `45668417667`）
- [x] `trade_calendar` 冷却哨兵修复（`monotonic` 开机秒数下 `0.0` 误判）

## 继承自 TODO.md 的未完成项（2026-11-26 归档，待逐项复核）

> 原 `TODO.md` 已删除；以下按主题保留未完成项，复核后再排期，过时项直接删除。

### 运维 / 无人值守

- [ ] `pre-commit install --hook-type pre-push`（D-1 用户侧，10 分钟）
- [ ] `logs/paper_daily_*.log` 轮转与清理（并入 `bootstrap_schedules.ps1`）
- [ ] 实时快照日期校验（`data_realtime.py` 目前只验列不验日期）

### 架构遗留

- [ ] `config.py` 拆包二阶段（`config/` 包已存在，需确认剩余项是否仍有效）
- [ ] 代码审查 Batch B：L8 扫描扩围 + WS 每连接全量构造、`backup_create` 阻塞、双实例限流等

### 产品 backlog（P33–P38，长期未排期）

- [ ] 快捷键自定义、帮助中心、反馈入口、更新日志页
- [ ] PWA、响应式表格、触摸手势、移动导航、离线模式、移动端推送
- [ ] 共享观察列表、信号评论、策略分享、团队仪表盘、活动日志、权限角色
- [ ] 视觉回归测试、Lighthouse 性能监控、用户行为分析
- [ ] paper trading 累计 60+ 天；5 批 commit 策略模板

## 归档规则

- 完成项保留 7 天后删除，只保留结论与日期；详细过程进 `CHANGELOG.md` 或提交信息。
