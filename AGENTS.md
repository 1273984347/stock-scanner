# AGENTS.md — AI 行为唯一真源（stock-scanner）

> **本文件回答一个问题：AI 在这个项目里必须怎么行动？**
> 硬约束、禁区、验收标准、命令入口**只在这里定义一次**；其它文件通过**引用**关联本文件，
> 不复制内容。修改规约改这里，别处只做链接。
>
> 职责边界（各文件回答的问题 / 何时看）见 `RULES.md` §文档关系表。
> 规约**为什么**这么定（真实踩坑）见 `docs/refactor/pitfalls.md`。

---

## 0. 开工前 3 条

1. **先读本文件的对应章节**，再改代码；改动涉及 `lib/`、`tests/`、CI 前先读
   `docs/refactor/pitfalls.md`（按症状查）。
2. **提交前必跑 §4 的 L0–L10 分层门禁，不可跳级**；门禁与 pytest **必须串行**。
3. **动手前先登记**：改动属新批次时按 §5 的 Task 流程建任务；跨会话接手先看
   `TODO.md` 与 `docs/refactor/contracts/` 的最新读数。

---

## 1. 目录结构与硬约束

### 1.1 顶层布局（改动前先看这张表）

```
stock-scanner/
├── api.py                # FastAPI 装配层（仅 app 定义 + 17 个 include_router；契约见 routers/）
├── routers/              # 域路由包：common.py 聚合 import/状态/helper，17 个域模块按 ROUTERS 归属
├── run.py / run_v2.py    # CLI 入口
├── config/               # 全局配置包（C11 拆包：_env/_types/_presets/_runtime/_constants）
├── scanner.py            # 选股策略入口
├── factors.py            # 因子库
├── lib/                  # 业务库，按职责分 8 个子包（见 1.3）
├── backtest/             # 回测引擎（unified.py 是唯一对外入口）
├── reporter/             # 报告生成与推送（scan_notify.py = 盘后信号推送）
├── scripts/              # 工具脚本（一次性任务/运维）
├── tests/                # 测试
├── static/               # 前端源码
├── docs/                 # 文档（含 refactor/ 度量基线与踩坑集）
├── cache/                # 运行时缓存与状态（gitignored，勿提交）
├── data/store/           # 项目内数据仓（ADR-0033：数据必须项目内落地）
├── hooks/                # git 钩子
├── AGENTS.md             # 本文件（AI 行为真源）
└── RULES.md              # 文档关系 + 目录级例外机制
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
- **禁止包级循环导入** —— 拆包前用 AST 建依赖图验证
- 已知必须同包的互反依赖对（拆包时不可拆开）：`broker ↔ data_layer`、`data_fund_flow ↔ data_layer`、`dynamic_exit ↔ partial_exit`、`market_utils ↔ tdx_batch`
- **机器判据（2026-11-20 起）**：方向约束由 `scripts/dep_direction_check.py` 强制——
  层级（低→高）`infra < data < {scoring, risk, portfolio, ml, monitoring} < strategy`；
  **低层 import 高层 = 违规**（同级横向合法，由环检测管辖）；lib 不得 import 根级业务
  模块（scanner 等）与顶层包（reporter/backtest），例外必须在该脚本 `EXCEPTIONS`
  登记 justification（新增未登记判红）。环检测已登记互反对：scoring↔strategy。
  **V1（data.broker→lib.risk 包级环）已由类型下沉解除**：Order/Trade/Holding →
  `lib.data.trade_datatypes`、ImpactConfig/MarketImpactModel → `lib.data.market_impact`、
  TradingConstraints → `lib.data.trading_constraints`；`from lib.risk import Order`
  聚合出口保留兼容（backtest/tests 在用）。

---

## 2. 专项规约

### 2.1 回测调用规约（RF-01 Phase 3 起强制）

| # | 规约 | 理由 |
|---|---|---|
| RB1 | **所有回测调用只经 `backtest.unified`**（`BacktestRunner` / `BacktestRequest`），**禁止**直接 import `backtest.core` / `backtest.engine` / `backtest.full` / `backtest.rebalance` | 回测口径只在一处定义；改动能被回测对拍基线捕获 |
| RB2 | 新增回测调用方时，**先确认 `BacktestRequest` 是否已有对应字段**；缺字段必须先补字段再补透传 | 漏传参数会**静默改变回测口径**，不报错 |
| RB3 | 需要**非归一化字段**（`cost_summary` / `perf_df` / `report` / `summary` / 顶层标量）时，取 `result.raw` 而非归一化结果 | 归一化只保留 `metrics` 子集，取错会丢字段 = 改对外契约 |
| RB4 | `backtest/unified.py` 的 `raw` 字段是「原始结果 dict」的**同一对象引用** | 保留归一化前的完整信息 |
| RB5 | `lib/monitoring/backtest_unified.py` 是**薄壳 re-export**（同一对象），新代码不要 import 它 | 兼容旧路径，不新增引用 |

> ⚠️ 已知引擎缺陷（2026-11-07 记录，未修）：`run_backtest` 的 `stock_codes` 是**必填位置参数**，
> `symbols` 只是 backward-compat 别名 ⇒ 只传 `symbols` 会抛
> `missing 1 required positional argument`。统一入口已同时传两者；
> 外部代码若直接调 `run_backtest`，**必须传 `stock_codes`**。

### 2.2 统一评分契约规约（RF-01 Phase 4 起强制）

| # | 规约 | 理由 |
|---|---|---|
| S1 | **归一的是契约不是算法**：7 个评分模块 / 9 个入口语义互异，禁止「合并成一个算法」 | 合并会静默改评分数值；判据修正见 `docs/adr/0003-*.md` |
| S2 | 评分结果统一经 `lib/scoring/scoring_contract.py::ScoringKernel` 分发；**适配器函数体不得含算术**（AST 硬守卫） | 权重算两次 = 口径漂移；`tests/features/test_scoring_contract.py` 自动判红 |
| S3 | `ScoringResult.raw` 必须是旧实现返回值的**同一对象引用** | 与 RB4 同构，是对拍取证的基准 |
| S4 | 四维总分唯一真源 = `scorer.comprehensive_score`（`PRIMARY_4D_SOURCE`）；评委团 / Agent / 机会分是**各自量纲的附加维度，不混算** | 计划 §三 Phase 4 第 4 点 |
| S5 | 权重定义只允许在 `lib/scoring/scoring_weights.py`；外部配置源必须在 `list_weight_sources()` 登记 | 消除「权重散落」的根因，而非只搬两个常量 |
| S6 | `FEATURE_UNIFIED_SCORING` **默认 off**；每切一个消费者前必须跑 `python scripts/scoring_baseline.py --check` | 开关是唯一回滚手段；阶段 B 逐消费者灰度 |
| S7 | 评分数值变化 > 1e-9 一律走 ADR + 迁移指南 | §3.3 B1 在评分域的具体化 |
| S8 | **评分路径判定只允许在 `lib/scoring/scoring_gateway.py`**；消费者不得读评分开关环境变量 / 调网关内部函数（P7-3 收口后 flag 已删，出现即回退），统一调 `score_4d(...)` | 判定散落 N 份就无法回答「哪几个消费者切了」；`tests/features/test_scoring_switch.py` 用 AST 守卫 |
| S9 | 每切一个消费者，必须跑 `python scripts/scoring_switch_diff.py --check`，且**自证 on 侧真的经过了内核**（内核调用数 off=0 / on>0） | 两侧都走旧实现时 diff 也恒为 0 ⇒ 空判据（坑表 P-56） |
| S10 | 切「自行算维度分的消费者」时，把维度提取**原样抽成纯函数**，消费者只调网关；**禁止把策略层函数注册成评分适配器** | 保持「适配器 = 评分实现」边界与 9 入口不变；抽取期间任何「顺手优化」都会改口径（ADR-0003 D5） |
| S11 | **纯透传消费者不加评分分支**：自身无评分调用点的端点登记进 `PASSTHROUGH_CONSUMERS`，改为纳入契约冻结验证（P7-3 收口后 L6 已退役）；禁止为清单打勾硬塞 `if` | `/api/score/{code}` 是三层纯透传，下游已接入 ⇒ 加分支零收益且破坏 S8（坑表 P-58） |
| S12 | 端点分类登记：`GRAY`（P7-3 收口后已全部走内核，类目保留于迁移指南）/ `PASSTHROUGH` / `INDIRECT` / `OUT_OF_SCOPE`（后三类互斥守卫仍在）。域外端点与缓存聚合端点**不做**端点实跑取证（缓存命中让 diff 恒 0 = 空判据），改为 AST 边界守卫 + L2 契约快照 | 坑表 P-59 / ADR-0003 D7 / ADR-0014；判据必须能区分「真的没变」和「压根没跑到」 |

> **阶段边界**：**Phase 4 阶段 B 已收官**（逐条核验见 `docs/plans/2026-11-07-refactor-plan.md` §七 收官验收）。
> 3 个灰度消费者 + 1 个透传 + 1 个间接 + 1 个域外端点全部登记并取证。
> **Phase 5 第 2 批已完成（拆 `api.py` 单体）**：`api.py` 从 3087 行收窄到约 200 行装配层，
> 68 端点按 17 域落 `routers/`；决策见 `docs/adr/0005-phase5-api-split.md`。
> **拆 `api.py` 后改动 `routers/` 仍必须跑 `python scripts/route_ownership_check.py --check`**；
> 鉴权依赖数不得减少，三条免鉴权端点（`POST /api/errors`、`GET /api/health`、
> `GET /api/scan/status`）不得新增。

### 2.3 异步与阻塞 IO 规约（RF-01 Phase 6 起强制）

| # | 规约 | 理由 |
|---|---|---|
| T1 | **`async def` 端点不得直接调用阻塞符号**（数据层取数 / parquet 读 / pandas 计算 / 文件写 / 网络）；必须经 `lib/infra/async_offload.py` 的唯一入口包裹 | 端点全为 async，阻塞调用会占住事件循环；契约快照单独冻结 async/sync 的原因见 `docs/refactor/README.md` §二 |
| T1-a | **非迁移端点**（WS 后台广播、backup 压缩）的 offload 允许用 `asyncio.to_thread`（已在 `OFFLOADED_TAILS` 登记），但必须被 L8 记为 `offloaded` 且有机器判据 | L9 契约已于 ADR-0021 拆分：`OFFLOAD_CONSUMERS` 不再强制等于 37 个 `MIGRATIONS`；新增消费者须走 `run_blocking` 且在 `RUNTIME_EVIDENCE` 登记可复现对拍测试（文件存在 + 点名 + 含 assert）。裸 `asyncio.to_thread` 仍允许但不能绕过 L8 视野 |
| T2 | **灰度判定只允许在 offload 入口**；消费者不得自读 `FEATURE_ASYNC_OFFLOAD`，开关默认 `all`（评审 P0-1 2026-11-20 翻转，前置 = audit 锁+WAL + L9/L10 门禁；回滚 = env **显式置 0**，删除变量 = 回默认 all），按消费者粒度切换仍受支持 | 与 S8 同构：判定散落就无法回答「到底哪几个消费者切了」 |
| T3 | **保持 `async def` 签名**，禁止把端点改成 `def` 来「顺手」走线程池 | 会静默改变 FastAPI 调度语义与并发上限；契约快照冻结 async/sync，L2 会判红 |
| T4 | 每切一个消费者必须跑 off/on 逐字段对拍，**并自证 on 侧真走了线程池**（off=0 / on>0） | 两侧都没切时 diff 也恒 0 = 空判据（坑表 P-56） |
| T5 | 阻塞判据以 `scripts/blocking_io_check.py` 的 **L8** 为准：**blocking 只准降 / offloaded 只准升 / other 逐字段冻结**；新符号必须显式归类后才能推进。**新增 HTTP 端点只允许走 `--recapture --mode refactor --allow-endpoints "METHOD /path"`**（该端点须零阻塞 + 显式声明 + 其余端点逐字段一致），禁止用 `--capture` 全量重建绕过取证（ADR-0026） | 计划的绝对阈值「≤1」被实测 242 推翻（ADR-0006 D1）；表外符号由 `other` 冻结兜住，不假设阻塞表完整 |
| T5-a | 端点发现不得只认带 `/api/` 前缀的 HTTP 装饰器；`websocket`、非 `/api/` 路由、`@app.middleware` 必须纳入扫描，且 **async 顶层 helper 也要展开** | `ROUTE_METHODS` 原来不含 `websocket`、路径硬过滤 `/api/`，`_helper_index` 只收 `ast.FunctionDef` ⇒ `/ws` 调的 async `_get_ws_data` 整条阻塞链（10 个调用）对门禁完全不可见（审查报告 §5 / 坑表 P-90） |
| T6 | **`_scan_tasks` / `_perf_metrics` 只允许经 `lib/infra/` 的带锁 API 读写**；`routers/` 中禁止直接下标或 `.get/.append/.clear/.update` | 散落读写会造成半更新状态与指标丢失；AST 守卫见 `tests/test_phase6_batch2.py`（ADR-0007 D4） |
| T6-a | `perf_metrics` 的 `_perf_metrics_lock` **只保护缓冲 list**；读旧文件 / json / 写盘移到独立的 `_flush_io_lock` 下，且 `record_metric` 必须在**锁外**触发 flush。`api_core` 缓存锁改**按 key 分片**（`_lock_for`），同一 key 仍串行 | P2-4/P2-5：单把全局锁把端点与多 WS 客户端串行化，中间件还在事件循环里同步读写全量 `perf_baseline.json`。见 `tests/features/test_batch_b_gates.py` |
| T6-b | dashboard 自动重扫描的在飞任务只允许经 `lib/infra/scan_task_registry.py` 的 `get/set/clear_inflight_scan` 带锁 API 读写；同一 preset 并发只起一个任务，超时**不 cancel** `to_thread` 线程，任务后台跑完自己写缓存 | `asyncio.wait_for(asyncio.to_thread(...))` 的线程不可取消，超时后并发请求各起一个重扫描并互相 `_cache_set` 覆盖；完成回调按 task 身份清除，防旧回调误清新任务（P2-6） |
| T7-a | **WS 凭据只走握手头**：`Authorization: Bearer` 或 `Sec-WebSocket-Protocol: bearer.<base64url(key)>`，禁止 URL query；**API 文档默认按环境开关**（生产设 `ENABLE_API_DOCS=0`）；**5xx 只回 `{detail, trace_id}`**，不回 `str(exc)`；`get_task` 的 error 统一替换成固定文案 | P1-2/P2-7/P2-8：URL 凭据会被反代 access log、浏览器历史、Referer 记录；`/openapi.json` 是未认证端点攻击地图；异常原文泄露内部路径与依赖名。判据见 `tests/features/test_batch_c_hardening.py` |
| T7-b | **部署形态默认单 worker**：启动期 `lib/infra/worker_guard.py` 同时探测命令行 `--workers` 与三个环境变量（命令行优先），>1 默认 fail-fast；确需多 worker 必须显式 `ALLOW_MULTI_WORKER=1`，由 `lib/infra/process_leader.py` 提供跨进程单飞（文件锁选主 + 原子快照 + 扫描槽租约），零 Redis。禁止以「广播去重」为名掩盖状态外置需求 | 实测 `--workers 2` 不写环境变量，只读环境变量会被一行命令绕过（P0 漏洞已修）；单客户端重复推送**不会**发生（连接按进程隔离），真问题是构造 ×N 与缓存分片。判据 `test_worker_guard.py` / `test_process_leader.py` / `test_multiprocess_integration.py`，坑表 P-95/P-97，ADR-0022 |
| T7-c | **导出字段统一过 `_csv_safe()`**：CSV/openpyxl 的字符串字段以 `= + - @` / 制表 / 回车开头时前置单引号；Excel 另行强制 `cell.data_type='s'`。数值字段不得包成文本 | P2-10：公式/DDE 注入。冻结迁移体因此偏离的，走 `MIGRATION_DIVERGENCES` 显式登记 + 行为判据，不许直接放宽逐字等价（ADR-0021，坑表 P-96） |
| T7-g | **数据必须项目内落地**：K 线/指数/快照/事件/财务/资金流先读 `data/store/`，在线源只作刷新与回填；不复权与前复权**分仓**，不得混存。取证链路与测试必须 `QUANT_LOCAL_STORE=off` | 2026-11-20 用户令「所有数据项目内都要有，不能只用在线数据源」；`cache/` 是 TTL 缓存、过期即删，不能当数据。判据 `tests/test_local_store.py` + `tests/test_local_first_read.py`，ADR-0033 |
| T7-f | **跨模块候选列表统一 `list[dict]`**：`ml_ranking["data"]` 与 `top10` 必须同形，禁止把 DataFrame 放进 `scan_result` 再做布尔判断或 JSON 序列化；无候选时统一 `None` | 2026-11-19 数据恢复后首次真跑崩在 `if ml_ranking.get("data")`（DataFrame 布尔判断抛 ValueError），三个消费方全按 list 写，只有 ML 分支被数据激活才暴露（P-101）。判据 `tests/test_ml_ranking_contract.py`、ADR-0028 |
| T7-e | **K 线源栈分两套，不复权源不得进回测栈**：默认栈 `sina_kline → tencent_kline → …`（覆盖含北交所/指数），回测栈 `tencent_kline → …`（唯一前复权）。新增 K 线源必须同时登记两个栈（或显式声明只进其一），并过 `scripts/kline_coverage_probe.py --strict` 的 6 标的日期判据。腾讯报文 rows 顺序是 日期/开/**收**/高/低/量、volume 是手，两处都必须按契约转换 | 2026-11-19 实测 8 个既有源全 0（baostock 黑名单 / push2his 不可达 / tushare 无权限 / 缺 Node / TDX 已删），而新浪/腾讯网关都通——「源栈全 0」不等于「没有数据」（P-99 第三次复发）。不复权价进回测会在除权日造假跳空且不报错。判据 `tests/test_kline_web_sources.py`、ADR-0027 |
| T7-d | **实时行情只允许一份实现**：编排/解析/单位归一/降级校验都在 `lib/data/data_realtime.py`；`data_layer.get_realtime_quotes` 只做委托，`data_api` 只做门面。**单位统一「股 / 元」**（腾讯手×100、万元×10000），**代码统一 6 位 + `市场` 列**；降级快照（<4000 行 / 缺列）逐条告警且**不写缓存**。数据源健康按 **host** 计数（`lib/data/host_health.py`，3 次 / 300s / 半开），禁止按「东财源」一票否决——`push2` 死时 `push2ex` 必须仍可用。**push2his/push2ex 在调用边界用 `call_with_health` / `guarded` 接入**（不注入 akshare）：异常必须原样抛出、`functools.wraps` 保留 `__module__`（限流分桶）；运维查看 `GET /api/system/host-health`（需鉴权，只读内存） | 三份腾讯解析 + 四套同名实时函数，其中 `data_ashare` 版只跑前 100 只；单位不归一让资金流 `vol_ratio` 差 100 倍、分支静默失效；代码带前缀让 `run.py`/`steps.py` 的精确匹配静默落空。判据 `test_realtime_snapshot_contract.py` / `test_host_health.py`，坑表 P-99，ADR-0024/0025。排查 host 用 `python scripts/host_health_probe.py` |

> **阶段边界**：**Phase 6 ✅ 收官（2026-11-12）**——L0–L10 全绿、Tier B 96 条 / 0 未分类、
> 候选 == {dashboard}（显式转 RF-02）。**Phase 7 待用户确认后进入**（不可逆）。
> 完整遗留清单见 `docs/refactor/contracts/blocking-io-tier-b.json`。
> 改 `routers/` 后必须同时跑 L7、L8、L9、L10。

---

## 3. 代码规约

### 3.1 import

| # | 规约 | 理由 |
|---|---|---|
| I1 | **一律用绝对导入 `from lib.<包>.<模块> import ...`** | 相对导入在拆包/改名时静默断裂 |
| I2 | **禁止 `from lib import X`**（X 为模块名） | 拆包后 `lib.X` 不再是模块；且 Python 会解析为包属性，报错信息误导 |
| I3 | **改模块名/挪文件时同步改全部引用**，不留薄壳 | 本项目 2026-11-07 起不再保留旧 import 路径 |
| I4 | **patch 目标是「使用方」的模块属性** | `from X import f` 已绑定引用，patch `X` 无效（pitfalls P-18/P-19） |
| I5 | **新增模块若与既有模块同名（如 `constraints`），必须改名消歧，不得合并** | 同名 ≠ 副本；合并会静默改变语义（pitfalls P-28） |

### 3.2 路径基准（**搬文件后必查**）

| # | 规约 |
|---|---|
| P1 | `lib/<包>/<模块>.py` 中访问仓库根目录资源，必须用 **`.parent.parent.parent`**（3 级） |
| P2 | 模块层级每变化一级，`Path(__file__).parent` 就要相应增减一级 —— **搬文件后逐个复核** |
| P3 | 路径基准的正确性由 `scripts/` 中的校验器或实测落点验证，**不靠肉眼数 `.parent`** |

> RF-01 重构共修正 **25 处** 路径基准。肉眼数层级极易错（我自己就数错过一次）。

### 3.3 行为不变

| # | 规约 |
|---|---|
| B1 | **重构不改数值口径** —— 评分、回测、指标数值变化 > 1e-9 容差即视为口径变更，须走 ADR |
| B2 | **既有测试的断言值不得修改**（改断言即视为口径变更；**例外**：规约本身变更时同步改断言并在提交信息写明依据） |
| B3 | 改动前先跑三把尺子取基线，改动后复跑对比（见 §4） |

---

## 4. 验证规约（**分层，不可跳级**）

### 4.1 提交前必跑（本地，零 CI 额度）

```powershell
# L0 语法
python -m compileall -q lib scripts tests reporter backtest

# L1 全量测试（当前基线：1721 passed / 12 skipped / 5 failed，2026-11-20；
#     5 failed 为 HEAD 既有，经 stash 同口径对照实验证实，非回归）
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
#    评分数值口径由 L5 继续锁定，历史取证见 docs/refactor/scoring-baseline.json 与 ADR-0014

# L7 端点归属表 + 契约基线（Phase 5 起；拆分前后必跑）
python scripts/route_ownership_check.py --check

# L8 阻塞 IO 基线（Phase 6 起；blocking 只准降 / other 冻结 / 尺子自证判红）
python scripts/blocking_io_check.py --check
python scripts/blocking_io_check.py --selftest

# L9 offload 开关对拍（Phase 6 第 2 批起；迁移 AST 等价 + off/on 响应 + 线程池自证）
# 覆盖 37 个迁移：3 回测（523b353）+ 第 4 批 5（77bc366）+ 第 5 批 5（6d0e434）
#   + 第 6 批 6（497550f）+ 第 7 批 18（c9b4d6f）
# RF-02/ADR-0015 + 批次 A/ADR-0020 演化：37 中 32 严格冻结逐字等价 + 5 有意分歧
#   （notifications / strategy_recommend / export_config），均因迁移完成后的有意行为修复
#   豁免逐字等价，仅校验接线完整性；行为正确性分别由
#   tests/features/test_rf02_bad_endpoints.py 与 tests/features/test_batch_a_security.py 覆盖。
#   分歧项新增时必须同步：清单 + ADR + 行为契约测试，不得只把端点从检查里摘掉。
python scripts/offload_switch_diff.py --check

# L10 并发压测 + Tier B 留痕（Phase 6 第 3 批起）
python scripts/concurrency_probe.py --check
# Tier B 清单由 python scripts/blocking_io_check.py --tier-b 生成，--check 内自动校验

# 文档结构自检（本次文档分层重构新增；见 §7 D7）
python scripts/doc_structure_check.py --check

# 数据源 host 级诊断（非门禁）：东财按 host 部分可用，别用「东财能不能通」下结论
# --strict 仅在「要求全部 host 可用」的部署环境使用（本机 push2 长期不可用）
python scripts/host_health_probe.py

# 日线覆盖诊断（非门禁；6 标的：沪/深/创业/科创/北/指数）
# 判据：每个标的的最新日期 == 最近交易日；--strict 时任一失败退出码 1
python scripts/kline_coverage_probe.py --strict
```

> **取证执行纪律**：L1（pytest 全量）与各门禁脚本**必须串行**执行。2026-11-19 曾在 L1
> 后台运行时并发跑 L2/L3/L7/L8，两者共享 `cache/` 与 ML 模型缓存，导致
> `test_clean_cache_falls_back_to_mtime_when_atime_unavailable` 与
> `test_gate_reports_no_drift_twice` 偶发假红；串行复跑即全绿。
> 「单跑绿、并发红」不是代码缺陷，也不许据此改基线或改判据。
> **门禁有副作用**：L9/L10 会打真实端点并留下暖缓存，**跑完 L9/L10 后再跑 L1 会假红**
> ⇒ 顺序固定为 **先 L1、后 L9/L10**（坑表 P-110）。

| 判据 | 通过标准 |
|---|---|
| L1 | 0 failed；用例数变化须能逐条解释（新增/改名/删除）。**基线里的既有 failed 不算本批引入** —— 用 `git stash` 同口径对照实验（stash → 测 → 还原）取证后再下结论 |
| L2 | `[PASS] contract unchanged`（当前 73 路由） |
| L3 | `[PASS] dual-run: 3 chains match baseline` |
| L4 | `[PASS] backtest baseline: all chains match` |
| L5 | `[PASS] scoring baseline: 9 sources match (285 samples)`，且每条链路 `mismatches = 0` |
| L6 | 已退役（P7-3 收口后 flag 删除，off/on 恒等 = 空判据）；数值口径由 L5 锁定 |
| L7 | `[PASS] route ownership: 69 endpoints / 17 routers / N frontend paths`；路径数由 `capture_frontend_paths()` 从 `static/` 源码重算，不在文档硬编码；鉴权依赖数不得减少、契约 diff = 0 |
| L8 | `[PASS] blocking-io: 69 endpoints / N blocking calls / 44 offloaded; extended 6 paths / 11 blocking frozen`。端点发现分两个互不干扰的集合：`endpoints`（HTTP `/api/`，成员+数量与 L7 对齐）与 `extended_endpoints`（`/ws`、非 `/api/` 路由、`@app.middleware` 中间件，独立冻结）。两者各自 blocking 只准降 / offloaded 只准升 / `other` 逐字段冻结 / 成员+数量双校验；`--selftest` 6 类注入回归 + 3 条 Tier B 语义断言全绿；Tier B 按**符号组合语义**分类 |
| L9 | `[PASS] offload switch: 32 endpoints migration-AST-equivalent + 5 intentionally diverged`；37 个迁移固定 commit 源码 AST 逐语句等价；off/on 整份响应字段顺序一致；线程池 off=0 / on=1 且工作线程与主线程不同；消费者登记与实际调用点双向对齐 |
| L10 | `[PASS] concurrency: 37 endpoints x 20 concurrent x 3 rounds`；P95 按**批次发起**计时、`on <= off × 1.10`（不得放宽阈值）、37/37 改善；前端路径「L7 扫描器重算 vs 登记清单」成员+数量双校验；Tier B 登记 0 未分类、新增阻塞符号未登记即判红 |
| 文档结构 | `[PASS] doc structure: 5 files / 0 dangling ref / 0 duplicated block`（见 §7 D7） |

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
| 改评分/回测口径 | ADR + §4.1 的 L3/L4/L5 三把尺子全跑 |
| 改文档结构 | §7 D7 文档结构自检 + 交叉引用不断链 |

### 4.5 对拍基线 pin 规约（RF-01 Phase 4 前置批次起强制）

| # | 规约 | 理由 |
|---|---|---|
| G1 | **基线捕获链路内禁止 `datetime.now()` / `date.today()`**；回测窗口起点必须由显式锚定常量推导并显式传参 | 2026-11-10 实测：未传 `start_date` 时 `engine.py:642` 用 `now()-320d`，`equity_curve[0]` 随日历漂移 3 天（pitfalls P-52） |
| G2 | **链路所有外部取数函数必须注入固定值**；模块级 from-import patch **使用方**，函数内延迟 import patch **定义方**，两处都要 pin | P-18/P-19 的复发；`get_stock_fund_flow` 未注入导致 `capital_flow` 4→5 |
| G3 | **基线判红时先证明漂移来源**，禁止改基线数字、更禁止挑一组「刚好能对上旧值」的注入值 | 为迎合脆弱判据补数据 = 补丁加补丁；正确顺序：修 pin → 重建无效基线 → 留 ADR |
| G4 | **重建基线必须字段级取证**：只有预期链路变化、其余链路逐字节一致；两次独立 `--capture` 的 `chains` 逐字节一致 | ADR-0003 |
| G5 | 每条 pin 规约必须有**机器判据**（`tests/test_baseline_pinning.py`），不能只写进文档等人记住 | 硬守卫优于文档提醒 |

---

## 5. 工作流

**开发流程**：`需求 → 验证 → 实现 → 回测 → 记录`
**调试流程**：`查 Skill → 诊断 → 修复 → 验证 → 沉淀`
**复杂调试**：`/systematic-debugging` 系统化排查

### 5.1 Task 驱动

**Bug 修复**
```
1. TaskCreate: "修复 <问题> - <简述>"
2. 诊断问题根源 (/systematic-debugging)
3. 修复 + pytest 验证
4. experience-capture 沉淀（进仓，见 §8）
5. git commit（显式 pathspec）
```

**策略/因子修改**
```
1. TaskCreate: "修改 <策略/因子>"
2. 修改 config/ 与 factors.py
3. pytest tests/ 验证
4. 回测验证（run.py 或 api）
5. git commit
```

**Task 状态取值**：`pending`（等待开始）/ `in_progress`（执行中）/ `completed`（已完成）。

### 5.2 强制触发规则

| 条件 | 必须执行 |
|:-----|:---------|
| 工作开始前 | 查自动记忆索引 + `pitfalls.md` 一句话索引按症状查（代码/CPU 密集型项目） |
| 数据源修改 | 测试多数据源降级 |
| 策略逻辑修改 | 回测验证 + Walk-forward |
| Bug 调试 >10min | `/experience-capture` |
| P0/P1 Bug | `/systematic-debugging`（诊断）+ `/verification-before-completion`（收口自证） |
| 新增 API 端点 | 过 API 安全 Checklist（输入校验→认证→鉴权→错误消息→资源限制） |

### 5.3 常见错误

| 症状 | 原因 | 解决 |
|:-----|:-----|:------|
| Tushare Token 过期 | 数据获取失败 | 更新 `.env` 中的 Token |
| 数据降级触发 | 高优先级数据源失败 | 检查网络/账户状态；**按 host 判**，别按源一票否决（T7-d） |
| 回测结果异常 | 涨停/停牌处理逻辑 | 检查 `lib/data/broker.py` |
| API 启动失败 | Port 被占用 | `netstat -ano \| grep :8000` |
| 扫描很慢 | 某数据源逐股重试 | 查 `cache/` 与熔断状态；批量层失效时退化到逐股（见 §6 资金流段） |

### 5.4 Session 结束检查

```
1. [ ] pytest tests/ 通过（或已用同口径对照实验证明失败为既有）
2. [ ] 回测结果无异常
3. [ ] git status 确认（无意外文件；用显式 pathspec 提交）
```

---

## 6. 执行纪律

### 6.1 数据源栈（约束，不是说明）

- **默认栈（实时）**：新浪 → 腾讯 → baostock → tdx → adata → westock → tushare → akshare → 本地 TDX
- **回测栈（前复权）**：腾讯 → ...（**新浪不进**——不复权在除权日造假跳空）
- **北交所**：新浪 → 腾讯 → 本地 TDX
- 真源 = `lib/data/data_source_registry.py`（ADR-0017/0027/0032）。8 个既有注册源
  2026-11 实测多数不可用，有效源 = 新浪/腾讯/westock 三条。自动降级 + 源级
  CircuitBreaker + host 级健康（`GET /api/system/host-health`）。
- **新增数据源必须同时登记两个栈**（或显式声明只进其一），见 T7-e。

### 6.2 资金流缺失语义（2026-11-20 P0 / 坑表 P-114）

代码归一单一真源 = `lib/data/market_utils.normalize_code()`（6 位纯数字）；
数据层缺失**显式化**——`FUND_FLOW_MISSING`（`main_net_ratio=None`）标记 +
`FundFlowUnavailableError` 抛错，**不再用全零冒充**（实测全零会以 1.0 分「假中性」
进入四维加权，与真实中性/强流出不可区分）；评分层对缺失维**剔除并归一其余三维权重**
（`breakdown.capital_flow=None` 可与 0 分区分）。

> 2026-11-20 实测同花顺即时资金流接口资金流列几乎全为 NaN，批量层实为失效，
> 逐股源为主要路径 —— 当时归因**源可用性问题**。
> **P-121（2026-11-21 二次修正）**：真根因 = THS 净额/成交额是**中文单位数字串**
> （"1.11亿"/"-9225.10万"），被 `pd.to_numeric` 整体 coerce 成 NaN——缺失是解析器
> 制造的假象，原始帧 0 NaN（坑表 P-121）。解析层已加 `_cn_number_to_float`；
> 逐股源为主要路径的旧口径作废。另：THS 周六晚间清算时段返回「--」占位，
> 可用性分钟级抖动——批量层失败语义照旧（缺失显式化 + 300s 标记）。

### 6.3 环境变量禁区

`.env` 文件包含敏感信息，**禁止 commit**：
`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `TUSHARE_TOKEN`（另见 §7 C5）。

### 6.4 API 服务管理

```bash
uvicorn api:app --host 127.0.0.1 --port 8000
```
> **勿在生产加 `--reload`**（watchfiles 热重载清空进程内任务态/缓存/WS 连接，与
> worker_guard 单进程前提冲突）；`--reload` 仅限开发期短时使用。

停止：`netstat -ano | grep :8000 | grep LISTEN` → `taskkill //F //PID <PID>`

### 6.5 维护提示

- `config/` 是全局配置核心（C11 拆包：域文件 _env/_types/_presets/_runtime/_constants），
  修改前确认影响范围；公共面变更必须同步契约 v2
- `lib/strategy/pipeline.py` 是策略编排核心，修改后运行回测验证
- 数据源适配器在 `lib/data/`，新增数据源时参考现有模式
- ML 模型存储在 `cache/models/`，更新后重新验证 Walk-forward

---

## 7. 文档与提交规约

| # | 规约 | 理由 |
|---|---|---|
| C1 | **改动与对应文档同批提交** —— 代码改了、文档没改 = 未完成 | |
| C2 | **提交用显式 pathspec**，禁 `git add -A` | 会带入 `backups/`、`skills/` 等无关目录 |
| C3 | **提交信息用中文**，格式 `<type>(<scope>): <说明>`，破坏性/目录级改动追加 `[skip ci]` | |
| C4 | **新增独立文档必须挂三处索引**：① 同目录 README 的目录内容表 ② 主计划文档对应章节 ③ **本文件 §8 关键文件索引**（CLAUDE.md 关键文件表已并入此处） | 只建文件不挂索引 = 没人找得到 |
| C5 | **`.env` 禁止 commit**；内容哈希写进版本化文件会触发 gitleaks（需精确 regex allowlist） | pitfalls P-44 |
| C6 | **删除已完成的功能模块须同步 `TODO.md`**（`[x]` → `[~]` 并注明移除日期与原因） | 避免台账与代码脱节 |
| D7 | **文档分层单一真源**：每类内容只在一个文件完整定义，其它位置只做链接/导入。AI 行为约束 ⇒ `AGENTS.md`（本文件）；例外机制 ⇒ `RULES.md`；人读入口 ⇒ `README.md`；经验 ⇒ `docs/refactor/pitfalls.md`。**禁止把同一段规约复制到两个文件** —— 复制即漂移。机器判据：`python scripts/doc_structure_check.py --check` | 两份副本必然漂移；用户会分不清哪条规则在生效 |

### 7.1 Git 提交节奏

| 场景 | 做法 |
|---|---|
| 一批改动 | **合并为一批、只跑一轮 CI** —— 拆分提交会让 CI 轮数成倍增加 |
| 文档类改动 | 追加 `[skip ci]`，不触发流水线 |
| 提交前 | `git status` 确认工作树无意外文件；用显式 pathspec |
| 推送后 | 确认本地 HEAD == `origin/master` |

### 7.2 遇到坑怎么办

1. 先查 `docs/refactor/pitfalls.md`（按症状查；条数以该文件头部「规模」行为唯一真源）
2. 再查 `docs/refactor/README.md`（尺子口径与自证记录）
3. 新坑 **追加** 到 `docs/refactor/pitfalls.md`（现象 → 根因 → 修复 → 推广），**不要改写已有条目** ——
   踩坑记录的价值在于保留了当时的错误判据
4. 若该坑的「预防」应改变 AI 的下一次行为 ⇒ **同时上提到本文件**（禁区/约定），
   并在该坑的「推广」段标注「已上提至 AGENTS.md §X」
5. **每个新坑必须在「推广」段标注自动化路径（三选一，缺一不收口）**：① 机器判据
   （进 `tests/` 或某把尺子）② 上提本文件 ③ 显式写「无法自动化」+ 一句触发场景。
   依据（2026-11-21 复盘）：复发坑全部发生在「判据未机器化 / 未覆盖新入口」的窗口
   （P-56 族×4 / P-68 族×4 / P-53×3 / P-99×3）；某域一旦机器化并覆盖全入口即停止
   复发（G1–G5 pin 漂移、P-66 文件锁）。流程入口：`/experience-capture` Skill
6. 工具链类经验同时沉淀到 Agent 记忆

---

## 8. 关键文件索引

| 文件 | 说明 |
|:-----|:-----|
| `config/` | 全局配置包（_env .env 解析 / _types dataclass / _presets 三模板 / _runtime get_config+JSON 热更新 / _constants）；公共面契约见 `docs/refactor/contracts/config-public-surface.json` v2 |
| `lib/data/market_utils.py` | **代码归一单一真源** `normalize_code()`（去 sh/sz/bj 前缀 + `zfill(6)`）+ 市场检测（坑表 P-114） |
| `lib/data/data_layer.py` | 多源数据层（`get_realtime_quotes` 只做委托，ADR-0024）；**资金流缺失显式化**：`FUND_FLOW_MISSING` + `FundFlowUnavailableError` |
| `lib/data/data_realtime.py` | **实时行情唯一实现**：sina 全市场 → 腾讯批量 → pytdx 样本；单位归一 + 6 位代码 + 降级校验（ADR-0024） |
| `lib/data/data_kline_web.py` | **网页 K 线源**（ADR-0027）：新浪不复权、腾讯 qfq（回测主力）；腾讯 rows 顺序与 volume 单位已钉 |
| `lib/data/data_source_registry.py` | **数据源注册真源**（ADR-0017/0027/0032）+ 源级 CircuitBreaker |
| `lib/data/host_health.py` | **host 级健康状态机**（3 次 / 300s / 半开）：东财按 host 部分可用（ADR-0025） |
| `lib/data/trade_calendar.py` | **交易日历单一真源**（P-108）：akshare 新浪日历 + 本地 JSON 缓存 + 刷取 daemon 守卫 |
| `lib/strategy/pipeline.py` | 策略编排核心 |
| `lib/scoring/scoring_contract.py` | **统一评分契约**：`ScoringResult` / `ScoringKernel` / 四维真源声明 |
| `lib/scoring/scoring_adapters.py` | 7 模块 / 9 入口的纯转发适配器 + `build_kernel()` |
| `lib/scoring/scoring_weights.py` | 评分权重唯一定义点 + 外部权重源登记 |
| `lib/scoring/scoring_gateway.py` | **评分路径唯一入口**：`score_4d(...)` |
| `lib/infra/async_offload.py` | **唯一 offload 入口**：`run_blocking(consumer, fn, ...)`，默认线程池；`OFFLOAD_CONSUMERS` 显式登记 |
| `lib/infra/scan_task_registry.py` | 扫描任务状态机 + 在飞任务注册表（带锁） |
| `lib/infra/worker_guard.py` | **多 worker 启动守卫**：`--workers`/env 探测，>1 默认 fail-fast |
| `lib/infra/process_leader.py` | **跨进程单飞协调**：文件锁 + 原子快照 + 扫描槽租约（零 Redis） |
| `reporter/scan_notify.py` | 盘后选股信号飞书推送/控制台输出（原 notifier.py） |
| `reporter/heartbeat.py` | **推送心跳哨兵**：末条成功推送 + 交易日历判定失联 |
| `scanner.py` | 选股策略入口 |
| `scripts/data_sync.py` | **本地仓同步/对账命令**（ADR-0033/C15）：kline 与 kline_raw 分仓同步（T7-g）+ fundflow 批量优先 + `--verify [--fix-manifest]` manifest 账实对账 |
| `scripts/offline_drill.py` | **断网演练判据**（C16）：QUANT_OFFLINE=1 + socket 全阻断下跑回测+扫描，验证本地仓端到端自给自足（与在线口径对照） |
| `scripts/scoring_baseline.py` | 第 5 把尺子：评分对拍基线（9 入口 × 285 样本）。**`--check` 的 diff 只打印前 30 条**，排查须自行导出全量 |
| `scripts/route_ownership_check.py` | 第 7 把尺子：17 域 / 69 端点归属 + 鉴权/async + 契约五重校验 |
| `scripts/blocking_io_check.py` | 第 8 把尺子 / L8 门禁（`--check` / `--selftest` / `--recapture`） |
| `scripts/offload_switch_diff.py` | 第 9 把尺子 / L9 门禁：迁移 AST 等价 + off/on diff + 线程池自证 |
| `scripts/concurrency_probe.py` | 第 10 把尺子 / L10：并发 P95 + 前端路径双校验 |
| `scripts/dep_direction_check.py` | §1.3 方向判据尺子：8 包方向 + 环检测 + 例外登记表 |
| `scripts/doc_structure_check.py` | 文档分层自检（§7 D7）：5 文件齐备 + 交叉引用不断链 + 无重复长段 |
| `docs/refactor/MIGRATION-GUIDE.md` | 迁移指南：RF-01 全部破坏点 before/after + 回滚方式 |
| `docs/refactor/pitfalls.md` | **踩坑集**（现象→根因→修复→推广）；条数以该文件头部「规模」行为唯一真源 |
| `docs/refactor/README.md` | 重构度量基线说明（契约/覆盖率/对拍三把尺子的口径与自证记录） |
| `docs/refactor/contracts/` | 各尺子的基线与取证 JSON（blocking-io / route-ownership / scoring / frontend-api-paths …） |
| `docs/plans/2026-11-07-refactor-plan.md` | RF-01 分阶段重构计划（§七 为逐批执行记录/验收/复盘） |
| `docs/audit/security-performance-review-2026-11-14.md` | 全仓安全/性能审查：2 条 P0 + 12 条 P1 + 15 条 P2 |
| `docs/audit/architecture-review-2026-11-20.md` | 全仓架构评审：P0×4 + P1/P2 + 扩展性上限 |
| `docs/adr/` | 架构决策记录（0001–0022，编号连续，破坏点均有对应 ADR） |
| `tests/features/test_batch_a_security.py` / `test_batch_b_gates.py` / `test_batch_c_hardening.py` | 批次 A/B/C 的机器判据（22 / 25 / 13 例） |
| `tests/features/test_scoring_contract.py` | 评分契约 AST 守卫 + 阈值边界冻结 |
| `tests/features/test_fund_flow_code_and_missing.py` | 资金流代码归一 + 缺失显式化判据（16 例） |
| `.env` | 敏感配置（禁止 commit） |
| `cache/models/` | ML 模型存储 |
