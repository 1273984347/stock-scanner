# RULES.md —— stock-scanner 项目规约

> 本文件是**项目级规约**，与 `CLAUDE.md`（工作流/架构说明）、`TODO.md`（需求台账）、
> `docs/refactor/pitfalls.md`（踩坑集）互不替代：
> - `CLAUDE.md` 说**怎么干活**（流程、命令、验证分层）
> - `RULES.md` 说**什么不许做 / 必须怎么做**（硬约束）
> - `docs/refactor/pitfalls.md` 说**为什么**（每条规矩背后的真实坑）
>
> 最后更新：2026-11-12（RF-01 Phase 6 ✅ 收官：37 端点 offload / blocking 242→107 / dashboard 转 RF-02 / 发现 3 个坏端点，ADR-0013；Phase 7 待确认）

---

## 一、目录结构规约

### 1.1 顶层布局（改动前先看这张表）

```
stock-scanner/
├── api.py                # FastAPI 装配层（仅 app 定义 + 17 个 include_router；契约见 routers/）
├── routers/              # 域路由包：common.py 聚合 import/状态/helper，17 个域模块按 ROUTERS 归属
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

## 一·五、回测调用规约（RF-01 Phase 3 起强制）

| # | 规约 | 理由 |
|---|---|---|
| B1 | **所有回测调用只经 `backtest.unified`**（`BacktestRunner` / `BacktestRequest`），**禁止**直接 import `backtest.core` / `backtest.engine` / `backtest.full` / `backtest.rebalance` | 回测口径只在一处定义；改动能被回测对拍基线捕获 |
| B2 | 新增回测调用方时，**先确认 `BacktestRequest` 是否已有对应字段**；缺字段必须先补字段再补透传 | 漏传参数会**静默改变回测口径**，不报错 |
| B3 | 需要**非归一化字段**（`cost_summary` / `perf_df` / `report` / `summary` / 顶层标量）时，取 `result.raw` 而非归一化结果 | 归一化只保留 `metrics` 子集，取错会丢字段 = 改对外契约 |
| B4 | `backtest/unified.py` 的 `raw` 字段是「原始结果 dict」的**同一对象引用** | 保留归一化前的完整信息 |
| B5 | `lib/monitoring/backtest_unified.py` 是**薄壳 re-export**（同一对象），新代码不要 import 它 | 兼容旧路径，不新增引用 |

> ⚠️ 已知引擎缺陷（2026-11-07 记录，未修）：`run_backtest` 的 `stock_codes` 是**必填位置参数**，
> `symbols` 只是 backward-compat 别名 ⇒ 只传 `symbols` 会抛
> `missing 1 required positional argument`。统一入口已同时传两者；
> 外部代码若直接调 `run_backtest`，**必须传 `stock_codes`**。

---

## 一·六、统一评分契约规约（RF-01 Phase 4 起强制）

| # | 规约 | 理由 |
|---|---|---|
| S1 | **归一的是契约不是算法**：7 个评分模块 / 9 个入口语义互异，禁止「合并成一个算法」 | 合并会静默改评分数值；判据修正见 `docs/adr/0003-*.md` |
| S2 | 评分结果统一经 `lib/scoring/scoring_contract.py::ScoringKernel` 分发；**适配器函数体不得含算术**（AST 硬守卫） | 权重算两次 = 口径漂移；`tests/features/test_scoring_contract.py` 自动判红 |
| S3 | `ScoringResult.raw` 必须是旧实现返回值的**同一对象引用** | 与 B4 同构，是对拍取证的基准 |
| S4 | 四维总分唯一真源 = `scorer.comprehensive_score`（`PRIMARY_4D_SOURCE`）；评委团 / Agent / 机会分是**各自量纲的附加维度，不混算** | 计划 §三 Phase 4 第 4 点 |
| S5 | 权重定义只允许在 `lib/scoring/scoring_weights.py`；外部配置源必须在 `list_weight_sources()` 登记 | 消除「权重散落」的根因，而非只搬两个常量 |
| S6 | `FEATURE_UNIFIED_SCORING` **默认 off**；每切一个消费者前必须跑 `python scripts/scoring_baseline.py --check` | 开关是唯一回滚手段；阶段 B 逐消费者灰度 |
| S7 | 评分数值变化 > 1e-9 一律走 ADR + 迁移指南 | §2.3 B1 在评分域的具体化 |
| S8 | **评分路径判定只允许在 `lib/scoring/scoring_gateway.py`**；消费者不得读评分开关环境变量 / 调网关内部函数（P7-3 收口后 flag 已删，出现即回退），统一调 `score_4d(...)` | 判定散落 N 份就无法回答「哪几个消费者切了」；`tests/features/test_scoring_switch.py` 用 AST 守卫 |
| S9 | 每切一个消费者，必须跑 `python scripts/scoring_switch_diff.py --check`，且**自证 on 侧真的经过了内核**（内核调用数 off=0 / on>0） | 两侧都走旧实现时 diff 也恒为 0 ⇒ 空判据（坑表 P-56） |
| S10 | 切「自行算维度分的消费者」时，把维度提取**原样抽成纯函数**，消费者只调网关；**禁止把策略层函数注册成评分适配器** | 保持「适配器 = 评分实现」边界与 9 入口不变；抽取期间任何「顺手优化」都会改口径（ADR-0003 D5） |
| S11 | **纯透传消费者不加评分分支**：自身无评分调用点的端点登记进 `PASSTHROUGH_CONSUMERS`，改为纳入契约冻结验证（P7-3 收口后 L6 已退役）；禁止为清单打勾硬塞 `if` | `/api/score/{code}` 是三层纯透传，下游已接入 ⇒ 加分支零收益且破坏 S8（坑表 P-58） |
| S12 | 端点分类登记：`GRAY`（P7-3 收口后已全部走内核，类目保留于迁移指南）/ `PASSTHROUGH` / `INDIRECT` / `OUT_OF_SCOPE`（后三类互斥守卫仍在）。域外端点与缓存聚合端点**不做**端点实跑取证（缓存命中让 diff 恒 0 = 空判据），改为 AST 边界守卫 + L2 契约快照 | 坑表 P-59 / ADR-0003 D7 / ADR-0014；判据必须能区分「真的没变」和「压根没跑到」 |

> **阶段边界**：**Phase 4 阶段 B 已收官**（逐条核验见 `docs/plans/2026-11-07-refactor-plan.md` §七 收官验收）。
> 3 个灰度消费者 + 1 个透传 + 1 个间接 + 1 个域外端点全部登记并取证；
> 端点侧评分接口形状已定 ⇒ 满足 §2.2 中 Phase 5 拆 `api.py` 的进入条件。
> **Phase 5 第 2 批已完成（拆 `api.py` 单体）**：`api.py` 从 3087 行收窄到约 200 行装配层，
> 68 端点按 17 域落 `routers/`（含 `common.py` 约 507 行聚合层）；决策见 `docs/adr/0005-phase5-api-split.md`。
> L0–L7 全 PASS（L1 **1262 passed / 12 skipped / 0 failed**；L2 72 routes diff=0；
> L7 68 端点 / 17 router / 36 前端路径，auth+async+contract 不变）。
> **拆 `api.py` 后改动 `routers/` 仍必须跑 `python scripts/route_ownership_check.py --check`**；
> 鉴权依赖数不得减少，三条免鉴权端点（`POST /api/errors`、`GET /api/health`、
> `GET /api/scan/status`）不得新增。

---

## 一·七、异步与阻塞 IO 规约（RF-01 Phase 6 起强制）

| # | 规约 | 理由 |
|---|---|---|
| T1 | **`async def` 端点不得直接调用阻塞符号**（数据层取数 / parquet 读 / pandas 计算 / 文件写 / 网络）；必须经 `lib/infra/async_offload.py` 的唯一入口包裹 | 68 端点全为 async，阻塞调用会占住事件循环；契约快照单独冻结 async/sync 的原因见 `docs/refactor/README.md` §二 |
| T1-a（B2） | **非迁移端点**（WS 后台广播、backup 压缩）的 offload 允许用 `asyncio.to_thread`（已在 `OFFLOADED_TAILS` 登记），但必须被 L8 记为 `offloaded` 且有机器判据 | L9 `check_consumer_registry()` 要求 `OFFLOAD_CONSUMERS` 严格等于 37 个 `MIGRATIONS`；新增灰度消费者会连迁移对拍结构一起判红。要正式纳入灰度须单独 ADR 扩展 L9 契约，不能偷偷塞进清单（ADR-0020 后续 3） |
| T2 | **灰度判定只允许在 offload 入口**；消费者不得自读 `FEATURE_ASYNC_OFFLOAD`，开关默认 off，按消费者粒度切换 | 与 S8 同构：判定散落就无法回答「到底哪几个消费者切了」 |
| T3 | **保持 `async def` 签名**，禁止把端点改成 `def` 来「顺手」走线程池 | 会静默改变 FastAPI 调度语义与并发上限；契约快照冻结 async/sync，L2 会判红 |
| T4 | 每切一个消费者必须跑 off/on 逐字段对拍，**并自证 on 侧真走了线程池**（off=0 / on>0） | 两侧都没切时 diff 也恒 0 = 空判据（坑表 P-56） |
| T5 | 阻塞判据以 `scripts/blocking_io_check.py` 的 **L8** 为准：**blocking 只准降 / offloaded 只准升 / other 逐字段冻结**；新符号必须显式归类后才能推进 | 计划的绝对阈值「≤1」被实测 242 推翻（ADR-0006 D1）；表外符号由 `other` 冻结兜住，不假设阻塞表完整 |
| T5-a（B1） | 端点发现不得只认带 `/api/` 前缀的 HTTP 装饰器；`websocket`、非 `/api/` 路由、`@app.middleware` 必须纳入扫描，且 **async 顶层 helper 也要展开** | `ROUTE_METHODS` 原来不含 `websocket`、路径硬过滤 `/api/`，`_helper_index` 只收 `ast.FunctionDef` ⇒ `/ws` 调的 async `_get_ws_data` 整条阻塞链（10 个调用）对门禁完全不可见（审查报告 §5 / 坑表 P-90） |
| T6 | **`_scan_tasks` / `_perf_metrics` 只允许经 `lib/infra/` 的带锁 API 读写**；`routers/` 中禁止直接下标或 `.get/.append/.clear/.update` | 散落读写会造成半更新状态与指标丢失；AST 守卫见 `tests/test_phase6_batch2.py`（ADR-0007 D4） |
| T6-a（B3） | `perf_metrics` 的 `_perf_metrics_lock` **只保护缓冲 list**；读旧文件 / json / 写盘移到独立的 `_flush_io_lock` 下，且 `record_metric` 必须在**锁外**触发 flush。`api_core` 缓存锁改**按 key 分片**（`_lock_for`），同一 key 仍串行 | P2-4/P2-5：单把全局锁把 68 端点与多 WS 客户端串行化，中间件还在事件循环里同步读写全量 `perf_baseline.json`。移锁后「不丢不重」由「锁内立即摘走快照 + 失败还回头部」保证，见 `tests/features/test_batch_b_gates.py` |
| T6-b（B4） | dashboard 自动重扫描的在飞任务只允许经 `lib/infra/scan_task_registry.py` 的 `get/set/clear_inflight_scan` 带锁 API 读写；同一 preset 并发只起一个任务，超时**不 cancel** `to_thread` 线程，任务后台跑完自己写缓存 | `asyncio.wait_for(asyncio.to_thread(...))` 的线程不可取消，超时后并发请求各起一个重扫描并互相 `_cache_set` 覆盖；完成回调按 task 身份清除，防旧回调误清新任务（P2-6） |

> **阶段边界**：**Phase 6 第 1 批 ✅**（第 8 把尺子 + 基线，零业务代码改动，ADR-0006）；
> **第 2 批 ✅**（`FEATURE_ASYNC_OFFLOAD` 默认 off + `lib/infra/async_offload.py` 唯一入口、
> 3 个 Tier A 回测端点 offload、共享状态收口加锁、L9 对拍，ADR-0007）。
> 实测：blocking **242 → 228**，offloaded **1 → 4**；两次基线重建均按 G4 字段级取证并留历史。
> **第 3 批 ✅**（TTL 缓存按 ADR-0008 裁剪、L10 并发压测、Tier B 201 条逐条留痕、
> 收官前核验表）；batch 3 结束时 Tier B 登记实测出
> **35 个 Tier A 候选端点尚未 offload**（ADR-0009 D5）。
> **第 4 批 ✅**（Tier A 候选 Top5 端点 offload + 两处门禁判据根因修，ADR-0010）：
> `factor_eval` / `factor_scatter` / `kline` / `risk` / `export_config`；
> L8/L9/L10 全绿，Tier B 候选 **35 → 30**（ADR-0010）。
> **第 5 批 ✅**（Tier A 候选 5 端点 offload + L9 无 try 迁移模式，ADR-0011）：
> `factor_contribution` / `market` / `positions` / `risk_exposure` / `stock`
> （**dashboard 因 try 体含 await 跳过**）；blocking **188 → 159**，offloaded **9 → 14**，
> Tier B 候选 **30 → 25**（ADR-0011）。
> **第 6 批 ✅**（Tier A 候选 6 端点 offload + 纯透传守卫随边界改写，ADR-0012）：
> `signals` / `exposures` / `position` / `regime_history` / `score` / `sector_flow`；
> blocking **159 → 141**，offloaded **14 → 20**，Tier B 候选 **25 → 19**（ADR-0012）。
> **第 7 批 ✅ + 收官**（18 个 1 调用端点 offload + dashboard 转 RF-02，ADR-0013）：
> blocking **141 → 107（起点 242，-56%）**，offloaded **20 → 38**，候选 **19 → 1**；
> **Phase 6 ✅ 收官（2026-11-12）**——L0–L10 全绿、Tier B 96 条 / 0 未分类、
> 候选 == {dashboard}（显式转 RF-02）。**Phase 7 待用户确认后进入**（不可逆）。
> 完整遗留清单见 `docs/refactor/contracts/blocking-io-tier-b.json`。
> 改 `routers/` 后必须同时跑 L7、L8、L9、L10。

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

# L1 全量测试（当前基线：1312 passed / 12 skipped，约 62s；2026-11-11 Phase 6 第 3 批复跑）
python -m pytest tests/ -q --tb=line -p no:warnings -o log_cli=false

# L2 契约快照（API 对外契约漂移门禁）
python scripts/contract_snapshot.py --check

# L3 双跑对拍（3 条链路逐字段比对，容差 1e-9）
python scripts/dual_run_diff.py --check --out docs/refactor/dual-run-baseline.json

# L4 回测口径基线（5 条链路：3 份 check_entry 判定序列 + 3 个引擎指标 + 统一入口 6 mode）
python scripts/backtest_baseline.py --check --out docs/refactor/backtest-baseline.json

# L5 评分对拍基线（9 个评分入口 × 285 样本，旧实现 vs 统一内核）
python scripts/scoring_baseline.py --check --out docs/refactor/scoring-baseline.json

# L6 评分开关对拍 —— P7-3（ADR-0014）已退役：flag 删除后 off/on 恒等 = P-54 空判据；
# 评分数值口径由 L5 继续锁定，历史取证见 docs/refactor/scoring-baseline.json 与 ADR-0014

# L7 端点归属表 + 契约基线（Phase 5 起；拆分前后必跑）
python scripts/route_ownership_check.py --check

# L8 阻塞 IO 基线（Phase 6 起；blocking 只准降 / other 冻结 / 尺子自证判红）
python scripts/blocking_io_check.py --check
python scripts/blocking_io_check.py --selftest

# L9 offload 开关对拍（Phase 6 第 2 批起；迁移 AST 等价 + off/on 响应 + 线程池自证）
# 覆盖 37 个迁移：3 回测（523b353）+ 第 4 批 5（77bc366）+ 第 5 批 5（6d0e434）+ 第 6 批 6（497550f）+ 第 7 批 18（c9b4d6f）
# RF-02/ADR-0015 + 批次 A/ADR-0020 演化：37 中 34 严格冻结逐字等价 + 3 有意分歧
#   （notifications / strategy_recommend / export_config），均因迁移完成后的有意行为修复
#   豁免逐字等价，仅校验接线完整性；行为正确性分别由
#   tests/features/test_rf02_bad_endpoints.py 与 tests/features/test_batch_a_security.py 覆盖。
#   分歧项新增时必须同步：清单 + ADR + 行为契约测试，不得只把端点从检查里摘掉。
python scripts/offload_switch_diff.py --check

# L10 并发压测 + Tier B 留痕（Phase 6 第 3 批起）
python scripts/concurrency_probe.py --check
# Tier B 清单由 `python scripts/blocking_io_check.py --tier-b` 生成，--check 内自动校验
```

| 判据 | 通过标准 |
|---|---|
| L1 | 0 failed；用例数变化须能逐条解释（新增/改名/删除） |
| L2 | `[PASS] contract unchanged`（当前 72 路由） |
| L3 | `[PASS] dual-run: 3 chains match baseline` |
| L4 | `[PASS] backtest baseline: all chains match` |
| L5 | `[PASS] scoring baseline: 9 sources match (285 samples)`，且每条链路 `mismatches = 0` |
| L6 | `[PASS] switch diff: 3 branch(es), 9 responses, 0 field(s) differ`；三条分支各自 `kernel calls off=0 on>0`，且 `[PASS] consumers: 3/3` |
| L7 | `[PASS] route ownership: 68 endpoints / 17 routers / N frontend paths`；路径数由 `capture_frontend_paths()` 从 `static/` 源码重算，不在文档硬编码；鉴权依赖数不得减少、契约 diff = 0 |
| L8 | `[PASS] blocking-io: 68 endpoints / 97 blocking calls / 39 offloaded; extended 6 paths / 11 blocking frozen`。**B1 起端点发现分两个互不干扰的集合**：`endpoints`（HTTP `/api/` 68 个，成员+数量与 L7 对齐，原口径不变）与 `extended_endpoints`（`/ws`、非 `/api/` 路由、`@app.middleware` 中间件，独立冻结）。两者各自 blocking 只准降 / offloaded 只准升 / `other` 逐字段冻结 / 成员+数量双校验；`--selftest` 6 类注入回归 + 3 条 Tier B 语义断言全绿；Tier B 按**符号组合语义**分类（目录遍历 + 压缩 ⇒ `compute_pending`）。**B2 起 `--recapture` 的 G4 字段级取证同时覆盖两个集合**（`_all_records`），扩展端点不再能被静默重建 |
| L9 | `[PASS] offload switch: 34 endpoints migration-AST-equivalent + 3 intentionally diverged`；37 个迁移固定 commit（`523b353` / `77bc366` / `6d0e434` / `497550f` / `c9b4d6f`）源码 AST 逐语句等价，3 项有意分歧仅校验接线完整性（见 ADR-0015 / ADR-0020）；off/on 整份响应字段顺序一致；线程池 off=0 / on=1 且工作线程与主线程不同；消费者登记与实际调用点双向对齐 |
| L10 | `[PASS] concurrency: 37 endpoints x 20 concurrent x 3 rounds`；P95 按**批次发起**计时、`on <= off × 1.10`（不得放宽阈值）、37/37 改善；前端路径改为「L7 扫描器重算 vs 登记清单」成员+数量双校验（禁止硬编码数量，禁止用清单自身 counts 自证）、`counts.normalized_paths == len(normalized_paths)` 自洽、off/on OpenAPI 一致；Tier B 登记 0 未分类、新增阻塞符号未登记即判红 |

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

### 4.5 对拍基线 pin 规约（RF-01 Phase 4 前置批次起强制）

| # | 规约 | 理由 |
|---|---|---|
| G1 | **基线捕获链路内禁止 `datetime.now()` / `date.today()`**；回测窗口起点必须由显式锚定常量推导并显式传参 | 2026-11-10 实测：未传 `start_date` 时 `engine.py:642` 用 `now()-320d`，`equity_curve[0]` 随日历漂移 3 天（pitfalls P-52） |
| G2 | **链路所有外部取数函数必须注入固定值**；模块级 from-import patch **使用方**，函数内延迟 import patch **定义方**，两处都要 pin | P-18/P-19 的复发；`get_stock_fund_flow` 未注入导致 `capital_flow` 4→5 |
| G3 | **基线判红时先证明漂移来源**，禁止改基线数字、更禁止挑一组「刚好能对上旧值」的注入值 | 为迎合脆弱判据补数据 = 补丁加补丁；正确顺序：修 pin → 重建无效基线 → 留 ADR |
| G4 | **重建基线必须字段级取证**：只有预期链路变化、其余链路逐字节一致；两次独立 `--capture` 的 `chains` 逐字节一致 | ADR-0003 |
| G5 | 每条 pin 规约必须有**机器判据**（`tests/test_baseline_pinning.py`），不能只写进文档等人记住 | 硬守卫优于文档提醒 |

> **已修复的历史误判（2026-11-15）**：原文曾写「`unified_modes` 链路的 `simple` mode
> 由 `datetime.now()` 推导 `start_date`，但指纹不含 equity 日期字段，故不判红」——
> 这个判断是**错的**：`metrics` / `trades` 本身随窗口变化，2026-11-15 跨日实测
> 交易数 6→5、26 个 `unified_modes.*.metrics` 字段漂移。
> 根因是 `BacktestRequest.start_date` 字段存在却未透传给 `engine.run_backtest`（B2）。
> 现已在 `backtest/unified.py` 的 `_run_simple` / `_run_full` 补透传，
> 并在 `scripts/backtest_baseline.py::_run_unified_modes` 显式传 `start_date=PIN_START_DATE`
> （`2025-12-22`，与 2026-11-07 基线采集窗口一致）。默认 `""` 时引擎仍走原逻辑，生产行为不变。
> 机器判据见 `tests/features/test_batch_b_gates.py`。

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

1. 先查 `docs/refactor/pitfalls.md`（按症状查；条数以该文件头部「规模」行为唯一真源，本文件不硬编码）
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
