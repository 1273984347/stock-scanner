# RULES.md —— stock-scanner 项目规约

> 本文件是**项目级规约**，与 `CLAUDE.md`（工作流/架构说明）、`TODO.md`（需求台账）、
> `docs/refactor/pitfalls.md`（踩坑集）互不替代：
> - `CLAUDE.md` 说**怎么干活**（流程、命令、验证分层）
> - `RULES.md` 说**什么不许做 / 必须怎么做**（硬约束）
> - `docs/refactor/pitfalls.md` 说**为什么**（每条规矩背后的真实坑）
>
> 最后更新：2026-11-07（目录重构批次）

---

## 一、目录结构规约

### 1.1 顶层布局（改动前先看这张表）

```
stock-scanner/
├── api.py                # FastAPI 应用入口（唯一对外契约载体）
├── run.py / run_v2.py    # CLI 入口
├── config.py             # 全局配置（策略/风控/LLM/ML）
├── scanner.py            # 选股策略入口
├── factors.py            # 因子库
├── lib/                  # 业务库，按职责分 8 个子包（见 1.3）
├── backtest/             # 回测引擎
├── reporter/             # 报告生成与推送
├── scripts/              # 工具脚本（一次性任务/运维）
├── tests/                # 测试
├── static/               # 前端源码
├── docs/                 # 文档（含 refactor/ 度量基线与踩坑集）
├── cache/                # 运行时缓存与状态（gitignored，勿提交）
├── config/               # 静态配置（judges.json）
├── hooks/                # git 钩子
└── RULES.md              # 本文件
```

### 1.2 硬约束

| # | 规约 | 理由 |
|---|---|---|
| D1 | **`lib/` 根目录不得再放业务模块** —— 业务代码一律进 8 个子包 | 2026-11-07 前 `lib/` 根平铺 126 个模块，新增模块无处安放 |
| D2 | **每个子包必须有 `__init__.py`**，且写明本包职责一句话 | 无 `__init__.py` 在部分导入路径下不可靠；职责缺失导致归属漂移 |
| D3 | **新增模块前先查归属**（下表 1.3），确无归属再新开包并在本文件登记 | 避免 8 个包各自长成第二个 `lib/` |
| D4 | **`cache/`、`dist/`、`logs/`、`node_modules/` 一律 gitignored** | 构建产物与运行时状态不应进版本库 |
| D5 | **根目录不得新增散落脚本** —— 工具进 `scripts/`，前端进 `static/` | 根目录每多一个文件，找它的人就要多翻一次 |
| D6 | **删除目录前先确认三态**：git 跟踪 / gitignore / untracked | 见 `docs/refactor/pitfalls.md` P-48 |

### 1.3 `lib/` 子包职责与归属表

| 子包 | 职责 | 典型模块 |
|---|---|---|
| `lib/data/` | 多源数据获取、缓存、限流、协议规范化 | `data_layer` `data_api` `data_protocol` `data_utils` `cache` `market_utils` `tdx_batch` `broker` |
| `lib/scoring/` | 评分体系：多维打分、因子、评委团、估值 | `scorer` `scorecard` `judge_panel` `fundamental` `technical_signals` `signal_tracker` |
| `lib/strategy/` | 选股策略、编排管道、调仓、因子策略 | `strategies` `pipeline` `steps` `optimizer` `risk_checker` |
| `lib/risk/` | 风控、仓位、约束、成本、滑点 | `risk_manager` `position_manager` `constraints` `selection_constraints` `datatypes` |
| `lib/portfolio/` | 组合构建/优化、IC 因子分析 | `portfolio` `portfolio_manager` `capital_allocator` `ic_calculator` |
| `lib/ml/` | 模型训练、推理、数据管道、在线监控 | `ml_trainer` `ml_predictor` `ml_data` `ml_monitor` |
| `lib/monitoring/` | 异常检测、告警、日志归档、市场体制 | `alert_monitor` `anomaly_detector` `audit_logger` `market_regime` |
| `lib/infra/` | 横切层：认证、通用工具、LLM 工具、共享状态 | `api_core` `utils` `llm_memory` `perf_metrics` `scan_task_registry` |

**依赖方向约束（拆包时必须满足）**：

```
infra  ←  data  ←  scoring  ←  strategy
                ↘  risk  ←  portfolio
                ↘  ml
```

- 允许 `infrastructure → data → 上层` 单向依赖
- **禁止包级循环导入** —— 拆包前用 AST 建依赖图验证（见 §四·V4）
- 已知必须同包的互反依赖对（拆包时不可拆开）：`broker ↔ data_layer`、`data_fund_flow ↔ data_layer`、`dynamic_exit ↔ partial_exit`、`market_utils ↔ tdx_batch`

---

## 二、代码规约

### 2.1 import

| # | 规约 | 理由 |
|---|---|---|
| I1 | **一律用绝对导入 `from lib.<包>.<模块> import ...`** | 相对导入在拆包/改名时静默断裂 |
| I2 | **禁止 `from lib import X`**（X 为模块名） | 拆包后 `lib.X` 不再是模块；且 Python 会解析为包属性，报错信息误导 |
| I3 | **改模块名/挪文件时同步改全部引用**，不留薄壳 | 本项目 2026-11-07 起不再保留旧 import 路径 |
| I4 | **patch 目标是「使用方」的模块属性** | `from X import f` 已绑定引用，patch `X` 无效（pitfalls P-18/P-19） |
| I5 | **新增模块若与既有模块同名（如 `constraints`），必须改名消歧，不得合并** | 同名 ≠ 副本；合并会静默改变语义（pitfalls P-28） |

### 2.2 路径基准（**搬文件后必查**）

| # | 规约 |
|---|---|
| P1 | `lib/<包>/<模块>.py` 中访问仓库根目录资源，必须用 **`.parent.parent.parent`**（3 级） |
| P2 | 模块层级每变化一级，`Path(__file__).parent` 就要相应增减一级 —— **搬文件后逐个复核** |
| P3 | 路径基准的正确性由 `scripts/` 中的校验器或实测落点验证，**不靠肉眼数 `.parent`** |

> 本次重构共修正 **25 处** 路径基准。肉眼数层级极易错（我自己就数错过一次）。

### 2.3 行为不变

| # | 规约 |
|---|---|
| B1 | **重构不改数值口径** —— 评分、回测、指标数值变化 > 1e-9 容差即视为口径变更，须走 ADR |
| B2 | **既有测试的断言值不得修改**（改断言即视为口径变更） |
| B3 | 改动前先跑三把尺子取基线，改动后复跑对比（见 §四） |

---

## 三、文档与提交规约

| # | 规约 | 理由 |
|---|---|---|
| C1 | **改动与对应文档同批提交** —— 代码改了、文档没改 = 未完成 | |
| C2 | **提交用显式 pathspec**，禁 `git add -A` | 会带入 `backups/`、`skills/` 等无关目录 |
| C3 | **提交信息用中文**，格式 `<type>(<scope>): <说明>`，破坏性/目录级改动追加 `[skip ci]` | |
| C4 | **新增独立文档必须挂三处索引**：① 同目录 README 的目录内容表 ② 主计划文档对应章节 ③ `CLAUDE.md` 关键文件表 | 只建文件不挂索引 = 没人找得到 |
| C5 | **`.env` 禁止 commit**；内容哈希写进版本化文件会触发 gitleaks（需精确 regex allowlist） | pitfalls P-44 |
| C6 | **删除已完成的功能模块须同步 `TODO.md`**（`[x]` → `[~]` 并注明移除日期与原因） | 避免台账与代码脱节 |

---

## 四、验证规约（**分层，不可跳级**）

### 4.1 提交前必跑（本地，零 CI 额度）

```powershell
# L0 语法
python -m compileall -q lib scripts tests reporter backtest

# L1 全量测试（当前基线：1157 passed / 12 skipped，约 13s）
python -m pytest tests/ -q --tb=line -p no:warnings -o log_cli=false

# L2 契约快照（API 对外契约漂移门禁）
python scripts/contract_snapshot.py --check

# L3 双跑对拍（3 条链路逐字段比对，容差 1e-9）
python scripts/dual_run_diff.py --check --out docs/refactor/dual-run-baseline.json

# L4 回测口径基线（4 条链路：3 份 check_entry 判定序列 + 3 个引擎指标）
python scripts/backtest_baseline.py --check --out docs/refactor/backtest-baseline.json
```

| 判据 | 通过标准 |
|---|---|
| L1 | 0 failed；用例数变化须能逐条解释（新增/改名/删除） |
| L2 | `[PASS] contract unchanged`（当前 72 路由） |
| L3 | `[PASS] dual-run: 3 chains match baseline` |
| L4 | `[PASS] backtest baseline: all chains match` |

### 4.2 覆盖率门禁

- **基线必须来自 CI 环境** —— 本地覆盖率与 CI 基线天然不同（本地有 TDX 数据/cache，可达路径更多）
- 本地跑覆盖率门禁报 `[FAIL]` 时，**先做同口径对照实验**（stash → 测 → 还原）再下结论
- 判据：同口径下 before == after 即非回归
- `--allow-modules` 用于「代码在模块间搬家」的结构变化豁免

### 4.3 CI 门禁

- 当前 `on:` 仅 `workflow_dispatch`（GitHub Actions 免费额度耗尽）
- **额度恢复后**：在 `.github/workflows/test.yml` 的 `on:` 下加回 `push:` / `pull_request:` 两行即可
- 推前本地跑 `scripts/ci_sim.py --ref HEAD` 全 PASS

### 4.4 改动前必须做的事

| 场景 | 必做 |
|---|---|
| 改策略/因子 | 回测验证 + Walk-forward |
| 改数据源 | 测试多数据源降级 + `as_of_date` 契约测试 |
| 挪文件/改包结构 | **AST 建依赖图验无循环** + 全量测试 + 契约 + 对拍 + **路径基准复核** |
| 新增 API 端点 | 安全 Checklist（输入校验 → 认证 → 鉴权 → 错误消息 → 资源限制） |

---

## 五、Git 提交节奏

| 场景 | 做法 |
|---|---|
| 一批改动 | **合并为一批、只跑一轮 CI** —— 拆分提交会让 CI 轮数成倍增加 |
| 文档类改动 | 追加 `[skip ci]`，不触发流水线 |
| 提交前 | `git status` 确认工作树无意外文件；用显式 pathspec |
| 推送后 | 确认本地 HEAD == `origin/master` |

---

## 六、遇到坑怎么办

1. 先查 `docs/refactor/pitfalls.md`（48 条，按症状查）
2. 再查 `docs/refactor/README.md`（尺子口径与自证记录）
3. 新坑 **追加** 到 `pitfalls.md`（现象 → 根因 → 修复 → 推广），**不要改写已有条目** ——
   踩坑记录的价值在于保留了当时的错误判据
4. 工具链类经验同时沉淀到 Agent 记忆

---

## 七、本文件与其它文档的关系

| 文档 | 回答什么问题 | 何时看 |
|---|---|---|
| `CLAUDE.md` | 怎么干活（流程/命令/验证分层） | 每次开工前 |
| `RULES.md`（本文件） | 什么不许做 / 必须怎么做 | 改代码前 |
| `TODO.md` | 要做什么、做到哪了 | 排期时 |
| `docs/refactor/pitfalls.md` | 为什么会踩坑 | 排查 bug 时 |
| `docs/refactor/baseline.md` | 当前的度量读数 | 对比增量时 |
| `docs/plans/2026-11-07-refactor-plan.md` | 重构的阶段计划与逐批记录 | 了解重构全貌时 |
