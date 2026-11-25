# Contributing

> 个人维护的 A 股量化项目，接受小而聚焦的改进。大改动请先开 Issue 对齐范围，
> 避免写完才发现方向不一致。

## 开发环境

```bash
python -m pip install -r requirements.txt
cp .env.example .env        # 填入本地需要的变量；.env 不会提交
```

Python 3.12 为 CI 基线；3.10 / 3.11 仅在手动 `workflow_dispatch` 时验证。

## 提交前

```bash
python -m compileall -q lib scripts tests reporter backtest
python -m pytest tests/ -q --tb=line -p no:warnings -o log_cli=false
python scripts/gates/doc_structure_check.py --check
python scripts/gates/dep_direction_check.py --check
python scripts/gates/contract_snapshot.py --check
```

改动涉及回测 / 评分 / 路由 / 阻塞 IO 时，按 `AGENTS.md` §4 跑对应 L2–L10 门禁；
门禁与 pytest 必须串行。`pre-push` 钩子会跑全量测试，失败先修根因，不要 `--no-verify`。

## 分支与提交

- 从 `master` 切分支，命名 `feat/...`、`fix/...`、`docs/...`
- 一个提交只做一件事；提交信息写“为什么”，不要只写“改了什么”
- 不提交 `.env`、密钥、数据导出、缓存与构建产物
- 改动行为时同步 `README.md` / `AGENTS.md` / `CHANGELOG.md`

## Pull Request

- 描述：问题、方案、验证命令与结果
- 涉及评分 / 回测口径变化时，附对拍结果；数值变化必须走 ADR
- CI 三平台（ubuntu / macos / windows × 3.12）需全绿

## 安全问题

不要用公开 Issue 报告漏洞，见 `SECURITY.md`。
