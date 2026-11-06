# 度量基线台账（RF-01 / Phase 0）

> 基线 commit：`a7c2265`（branch `master`）｜ 建立日期：2026-11-07
> 用途：各 Phase 的**增量对比参照**。任何阶段收官时更新「当前值」列，并把差值写入对应 Phase 的执行记录。

---

## 一、规模基线

| 指标 | 基线值 | 取证方式 |
|---|---:|---|
| Python 文件数 | 312 | `Get-ChildItem -Recurse -Filter *.py` |
| Python 总行数 | 62,944 | 同上 + `Measure-Object -Line` |
| `lib/` 业务模块数 | 118（+ `__init__.py` = 119 文件） | `Get-ChildItem lib` |
| `lib/` 无同名测试模块 | **74 / 118（63%）** | 模块名 ↔ `tests/test_*.py` 映射 |
| `api.py` 行数 | **3,142** | `(Get-Content api.py).Count` |
| `backtest/` 业务模块数 | 9 | 目录列举 |
| 测试文件数 | 116 | `Get-ChildItem tests` |
| 测试用例数 | **1,132** | `pytest --collect-only -q`（收集耗时 5.72s） |
| `cache/` parquet 文件数 | 3,423（27 个子目录） | 递归统计 |
| 前端消费的 API 路径 | **18** | `static/**` 的 `/api/` 路径去重 |

---

## 二、契约基线（`scripts/contract_snapshot.py` 产出）

快照文件：`contracts/route-contract-baseline.json` / `contracts/openapi-baseline.json`

| 指标 | 基线值 |
|---|---:|
| `app.routes` 总数 | 78 |
| `/api/*` 业务端点 | **68** |
| 非 `/api` HTTP 端点（`/`、`/favicon.svg`、`/sw.js`） | 3 |
| WebSocket（`/ws`） | 1 |
| 静态 Mount（`/css`、`/js`） | 2 |
| FastAPI 内置 | 4 |
| 方法分布（`/api/*`） | GET 49 / POST 15 / DELETE 3 / PATCH 1 |
| **async / sync** | **68 / 0** |
| 需鉴权端点 | 65 |
| 免鉴权端点 | 3：`POST /api/errors`、`GET /api/health`、`GET /api/scan/status` |
| AST ↔ 运行时端点集合互证 | 72 ↔ 72，零差异 |

### 契约口径陷阱（务必记住）

`grep -c '@app\.(get\|post\|put\|delete)' api.py` = **68**，与 `/api/*` 路由数 **68 数字巧合相等但集合不同**：
前者含 `/`、`/favicon.svg`、`/sw.js`，且**漏 `PATCH /api/alerts/rules/{rule_id}`**。
⇒ **端点数一律以契约快照工具的运行时口径为准**，勿用正则数端点。

---

## 二·0、CI 基线（2026-11-07 实测，run `42289383981` / ubuntu-latest 3.12）

| 指标 | 读数 |
|---|---|
| pytest 用例总数 | 1,132（**1,117 passed + 3 failed + 12 skipped**） |
| pytest 耗时（CI 干净环境） | **110.69s** |
| 失败用例 | 全部集中在 `tests/test_signal_tracker.py::TestBorrow2AtomicWrite`（3 个） |
| 本地全量耗时 | **> 28 min 未结束**（被主动取消） |

**本地 vs CI 相差 15 倍以上** —— CI 环境无本地 TDX 数据、无预取缓存，110s 跑完；
本地跑 28 分钟未完 ⇒ 本地存在走真实数据/网络路径的慢用例。Phase 0 剩余项需定位这些用例
（`pytest --durations=20`），否则本地开发与 CI 的行为差异无法解释。

### 既有 CI 红（非本次引入，已存在）

3.10 / 3.11 的 job 同样失败在 `Run tests`，与契约门禁无关：
- `test_concurrent_save_history_no_data_loss` —— 并发写入丢失 400 条 records
- `test_portalocker_lock_acquired_and_released` —— 断言 `portalocker.Lock must be called`
- `test_portalocker_timeout_returns_gracefully` —— 期望抛 `OSError` 但未抛

根因方向：项目在 `.pre-commit-config.yaml` 注明 portalocker 为「Windows 优先，Linux fallback fcntl」，
而这 3 条断言 **portalocker 必被调用** ⇒ Linux 上的 fcntl fallback 与测试断言不匹配。
**属 Phase 0 待修的既有问题**，与契约快照工具无关；在此之前，
CI 的整体结论不可用于判断契约门禁，**必须看步骤级结论**。

---

## 三、覆盖率基线（⏳ 待补 —— Phase 0 剩余项）

> 目标：CI 增加 `pytest --cov`，产出按模块分解的覆盖率基线，**先只报不卡**。
> 待补读数（见 Phase 0 退出判据）：

| 指标 | 基线值 |
|---|---:|
| 总覆盖率 | 待补 |
| `lib/` 分模块覆盖率（按模块列，不只报总数） | 待补 |
| `api.py` 覆盖率 | 待补 |
| `backtest/` 覆盖率 | 待补 |
| 零覆盖模块清单 | 待补 |

**已知盲区**（2026-11-07 实测）：118 个 `lib/` 模块中 **74 个（63%）无任何同名测试**，
其中 28 个数据层模块完全裸奔；ML 层（`ml_predictor / ml_trainer / ml_monitor / model_registry`）、
组合与风控辅助层（`portfolio* / risk_parity / cost_filter / slippage_model / twap`）、
评委层（`judge_panel / judge_strategies`）均无安全网。

---

## 四、双跑对拍框架（⏳ 待补 —— Phase 0 剩余项）

> 目标：同一进程内同时执行旧/新实现，产出字段级 diff。
> **硬约束（写框架时必须内置）**：
> ① `pin` 数据源（固定 `tdx_local_qfq` 等本地源）与 `as_of_date` —— 网络源两次取数必然不同，直接对拍必假红；
> ② 对拍双方**共享同一份已加载的数据**，不得各自取数；
> ③ 浮点对比**逐列比 dtype 与列序**，不只比数值（schema 变化会静默污染下游因子与回测数值）。

| 对拍目标 | 旧实现 | 新实现 | 容差 | 状态 |
|---|---|---|---|---|
| `/api/scan` | 待定 | 待定 | 1e-9 | 待建 |
| `/api/score/{code}` | 待定 | 待定 | 1e-9 | 待建 |
| `/api/backtest` | 待定 | 待定 | 1e-9 | 待建 |

---

## 五、阶段增量对照（随各 Phase 收官回填）

| Phase | 关键指标 | 基线 | 目标 | 当前 | 状态 |
|---|---|---:|---:|---:|---|
| 0 | 契约快照覆盖端点数 | — | 68 | 68 | ✅ 完成 |
| 0 | 尺子自证（判红能力） | — | 3 路验证 | 3 路通过 | ✅ 完成 |
| 0 | **CI 门禁三平台可用** | — | ubuntu/macos/windows 全绿 | 3/3 success | ✅ 完成 |
| 0 | CI job 数增量 | 9 | 9（不新增） | 9 | ✅ 完成 |
| 0 | 覆盖率基线入库 | — | 按模块分解 | — | ⏳ 待补 |
| 0 | 对拍框架可用 | — | 3 条链路 | — | ⏳ 待补 |
| 0 | 零测试数据模块契约测试 | — | ≥5 | 0 | ⏳ 待补 |
| 1 | `api.py` 模块级可变状态 | 3 | 0 | 3 | ⏳ 待开始 |
| 2 | 数据层零测试模块数 | 28 | 0 | 28 | ⏳ 待开始 |
| 3 | `backtest/` 重复实现函数 | ≥3 | 0 | — | ⏳ 待开始 |
| 4 | 评分实现套数 | 8 | 1 内核 + N 适配器 | 8 | ⏳ 待开始 |
| 5 | `api.py` 行数 | 3,142 | ≤300 | 3,142 | ⏳ 待开始 |
| 5 | 端点契约 diff | — | 0 | 0（基线） | ⏳ 待开始 |
| 6 | async 端点内阻塞调用数 | 待测 | ≤1 | — | ⏳ 待开始 |
| 7 | 旧符号全仓残留 | — | 0 | — | ⏳ 待开始 |
