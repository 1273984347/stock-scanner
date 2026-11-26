# TODOS

> 任务唯一台账。每次操作后更新；完成项标 `[x]`，当日结束前归入「已完成」，
> 避免重复排期。最近更新：2026-11-26 17:40。

## 进行中

- [ ] 推送本批回归修复并确认公开仓 CI 三平台绿

## 待办

- [ ] Go CLI 鉴权态真实命令回归（完成专属 APIKey 授权后再验）
- [ ] 公开仓 CI 首周观察：失败按根因修，不通过放宽判据或改基线压红
- [ ] 公开/内部文档边界复盘：评估 `docs/adr`、`docs/plans` 中可逐步公开的部分
- [ ] 复核继承 backlog（见下节），删除过时项后再排期

## 已完成（2026-11-26）

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
