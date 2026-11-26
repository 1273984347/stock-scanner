# HANDOVER

> 接手顺序：本文件 → `AGENTS.md`（AI 行为真源）→ `TODOS.md`（任务台账）。
> 最近更新：2026-11-26 18:05。

## 当前状态

- 仓库：<https://github.com/1273984347/stock-scanner>（**PUBLIC**，默认分支 `master`）
- 公开范围：代码、`tests/`、CI、`README.md`、`AGENTS.md`、`CLAUDE.md`、`RULES.md`、
  治理文件（`LICENSE` / `SECURITY.md` / `CHANGELOG.md` / `ROADMAP.md` /
  `CODE_OF_CONDUCT.md` / `CONTRIBUTING.md` / `TODOS.md` / `HANDOVER.md`）、
  `docs/refactor/`、`docs/adr/0008-phase6-cache-scope.md`
- 不在公开仓：内部 `docs/plans|research|archive|audit`、`.claude/`、`.superpowers/`、
  `data/` 导出与本地备份
- CI：GitHub Actions，`push` / `pull_request` / `workflow_dispatch`，三平台 × Python 3.12；
  本批提交 run `45673551198` 与记录提交 run `45675590410` 均三平台全绿；新回归待推
- 本地工作区：`D:/stock-scanner`，本地 `master` 领先远端 1 个提交 `739baa9`；
  远端 HEAD `62b1899`；`git push origin master` 两次被 HTTPS 代理 502 阻断，待网络恢复重试

## 关键约定

- 门禁统一走 `scripts/gates/*.py`；`scripts/*.py` 旧路径为兼容硬链接，发版周期后移除
- 提交前跑 L0–L10；`pre-push` 会跑全量测试，红灯不得用 `--no-verify` 绕过
- `.env` 未跟踪；仓库禁止提交密钥（gitleaks pre-commit）
- 任务台账是 `TODOS.md`，不要再新建 `TODO.md`
- 公开仓标准 runner 免费；不要为了省额度关掉 `push` / `pull_request` 触发

## 下一步

1. 网络恢复后执行 `git push origin master`，确认 CI 三平台绿
2. 鉴权态回归已就绪：默认 CI 跑 fake CLI 解析路径；真实网络需
   `WESTOCK_GO_AUTH_TEST=1 python -m pytest tests/test_westock_six_categories.py -q`
3. 公开仓 CI 首周观察；失败按根因修，不压基线

## 最近变更（摘要）

- 2026-11-26：新增鉴权态回归测试（CI 确定性 + 真实网络 opt-in），本机实跑 30.5s 通过

- 2026-11-26：Go CLI 未鉴权真实回归完成；一次性授权 URL 从日志脱敏；SECURITY 增加报告模板

- 2026-11-26：westock 六类命令 WIP 提交（`051eb31`）；`TODO.md` 归档进 `TODOS.md` 后删除；
  补齐治理文件；`.env` 清理 `FEISHU_*`
- 2026-11-26：仓库删除重建为干净公开仓；旧历史对象不可达；CI 三平台硬化并全绿；
  飞书应用已删除
- 2026-11-25：12 个门禁 `ROOT` 改为标记法自锚定；`scripts/gates/` 纳入版本控制
