# Changelog

本项目从 2026-11-26 起按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)
格式记录；日期为 ISO 8601。早期历史以提交记录为准。

## [Unreleased]

### Added

- westock Go CLI 六类数据命令封装：`get_hot` / `get_lhb` / `get_news` / `get_report` /
  `get_notice` / `get_sector_rank` / `get_index_constituent` / `get_macro` /
  `get_fund_north` / `get_fund_south` / `get_etf`，未安装或未鉴权时降级为空结果。
- `tests/test_westock_six_categories.py`：命令 argv 链契约 + 降级路径测试。
- `SECURITY.md`：漏洞报告模板（复制填写 + 脱敏要求）
- 鉴权态回归测试：CI 用 fake CLI 验证解析路径；真实网络 opt-in
  （`WESTOCK_GO_AUTH_TEST=1`），本机实跑 11 条命令全绿
- 治理文件：`TODOS.md`、`HANDOVER.md`、`SECURITY.md`、`ROADMAP.md`、
  `CODE_OF_CONDUCT.md`、`CONTRIBUTING.md`、`LICENSE`（MIT）。

### Changed

- 公开仓重建为干净历史（2026-11-26）：旧对象不可达；公开范围收敛为代码、测试、CI、
  README/AGENTS/CLAUDE/RULES、`docs/refactor` 与 ADR-0008。
- 门禁统一走 `scripts/gates/*.py`；`AGENTS.md` 命令同步切换。
- `AGENTS.md` / `CLAUDE.md` / `RULES.md` 的任务台账引用由 `TODO.md` 切到 `TODOS.md`；
  `TODO.md` 的未完成项已归并进 `TODOS.md` 后删除。

### Fixed

- `trade_calendar` 冷却哨兵由 `0.0` 改为 `-inf`：Linux/macOS 的 `monotonic()` 是开机秒数，
  新机器前 300 秒会被误判为「冷却中」，导致交易日历永不刷新。
- Windows CI 下 `git show` 历史源码改为显式 UTF-8 解码，修复 `ast.parse` TypeError。
- Go CLI 未鉴权日志脱敏：`AUTH_REQUIRED` 一次性授权 URL（含 `code=`）不再写入日志；
  新增未鉴权全能力降级回归测试。
- 鉴权态测试夹具改用 ASCII 输出，兼容 Windows runner 的 cp1252 stdout。
- L9 迁移锚点同步为重写后的提交 SHA；CI 增加 `fetch-depth: 0` 读取历史。
- 锁定 `akshare==1.18.60`（1.19 移除 `stock_zh_a_spot`）；补充 `pytest-asyncio`、`baostock`。
- 进程锁测试改为 release event 控制持锁期，去掉固定 `sleep` 的时序依赖。

## 2026-11-25

### Changed

- 12 个门禁脚本 `ROOT` 改为标记法自锚定，支持目录移动；`scripts/gates/` 纳入版本控制。

## 2026-11-24 及更早

早期重构、数据仓建设与策略迭代见 git 提交记录与 `docs/refactor/pitfalls.md`。
