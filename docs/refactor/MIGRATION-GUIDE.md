# RF-01 迁移指南（MIGRATION-GUIDE）

> **最后更新**：2026-11-12（RF-01 Phase 7 · P7-1 批）
> **性质**：RF-01 分阶段重构（Phase 0–7）的全部破坏点汇总 + before/after + 迁移步骤。
> **判据**：破坏点判定标准见 `docs/plans/2026-11-07-refactor-plan.md` §4.3（六类触发 ADR）；
> 每个破坏点的决策全文在 `docs/adr/0001-0013`。
> **纪律**：先发本指南、再删代码（计划 §三 Phase 7）；残留扫描 = 全仓 grep 旧符号命中 0。

---

## 一、总览：兼容层现状

| 阶段 | 改了什么 | 过渡层现状 | Phase 7 处置 |
|---|---|---|---|
| Phase 0 | 度量基线 + 十把尺子（L0–L10）+ 默认禁网 fixture | 门禁本体，非过渡层 | **永久保留**（回归防线） |
| Phase 1 | `api.py` 单体可变状态抽到 `routers/common.py` + 带锁 API | 已收口（AST 守卫） | 保留 |
| Phase 2 | 数据层统一：`lib/data/data_layer.py` 8 源 registry + 断路器 | 对外行为零变化（L3 双跑对拍锁口径） | P7-2 复核 alias 转发链 |
| Phase 3 | 回测引擎统一：`backtest/` 包唯一入口 | `backtest/full.py` / `rebalance.py` 为 deprecated wrapper（仍被 unified/engine/__init__ 引用）；`backtest/core.py` 的 deprecation 标注**是误导**（`run_factor_backtest` 仍是活实现） | P7-2 收敛 |
| Phase 4 | 评分统一：`ScoringKernel` + 9 入口适配器 + `FEATURE_UNIFIED_SCORING` 灰度（默认 off，GRAY=strategies/pipeline/cli） | 灰度开关仍在；数值 0 差异（L5 285 样本 / L6 三分支） | P7-3 出 ADR 收口灰度 |
| Phase 5 | `api.py` 3087 行拆为 17 域 router + 181 行装配层 | 对外 HTTP 契约 0 漂移（L2/L7） | P7-1 已清不可达死代码 |
| Phase 6 | 38 端点异步 offload（`FEATURE_ASYNC_OFFLOAD`，默认 off） | 开关是**运行时回滚能力**，非过渡层 | **永久保留**（回滚 = 环境变量） |
| Phase 7 | 本文件 + 旧实现清理 | 见 §三 路线图 | 进行中 |

**对外 HTTP 契约**：Phase 0–7 全程零破坏（L2 契约快照 72 routes 每批 diff=0；唯一的
预存在坏端点发现见 §二 P75）。前端 36 条 API 路径每批冻结（L10）。

---

## 二、Breaking changes 表

> 每条含：变更点 / 影响面 / Before / After / 迁移步骤 / 生效版本 / 回滚方式。
> 「内部」= 仅仓内调用方受影响；本项目无外部 API 消费方（前端经同一仓发布）。

### BC-01 · 市场体制检测统一到 `lib.monitoring.market_regime`（Phase 3 / P7-1 删除壳）

- **变更点**：`factors.detect_market_regime()` 与 `factors.MarketRegime` dataclass 已删除。
- **影响面**：内部调用方（清点时全仓引用 = 0，见 §四 证据）；无对外影响。
- **Before**：
  ```python
  from factors import detect_market_regime   # DeprecationWarning + 转发
  r = detect_market_regime(prices)           # → factors.MarketRegime
  ```
- **After**：
  ```python
  from lib.monitoring.market_regime import detect_market_regime
  result = detect_market_regime(df)          # → dict（regime/confidence/indicators）
  ```
  注意：新来源返回 **dict**（`result["regime"]`），旧壳返回 **dataclass**；
  测试与生产代码均已在新来源上（`tests/test_market_regime*.py`）。
- **迁移步骤**：改 import 行；取值从属性改下标。
- **生效版本**：Phase 7 P7-1（2026-11-12）。
- **回滚方式**：`git revert` P7-1 提交（壳为纯转发，无数据迁移）。

### BC-02 · `api.py` 拆分为 17 域 router（Phase 5）

- **变更点**：68 个端点从 `api.py` 单体迁至 `routers/{scan,scoring,stock,market,portfolio,risk,
  factor,backtest,analysis,export,settings,signals_audit,strategy,alerts,backup,notifications,system}.py`；
  `api.py` 只剩 app 装配（181 行）。
- **影响面**：内部调用方；直接 `from api import <私有符号>` 的脚本（横切函数已移至
  `routers/common.py`，`api.py` 经 `from routers.common import *` 继续再导出）。
- **Before**：所有 handler / `_cache_get` / `_get_orchestrator` 等定义在 `api.py`。
- **After**：`from routers.common import *` 提供横切符号；端点在各域 router；
  `from routers.<域> import <端点函数>` 可直接调用（L9/L10 取证即此方式）。
- **迁移步骤**：import 改指 `routers.common` 或对应域模块。
- **生效版本**：Phase 5（ADR-0004 / ADR-0005）。
- **回滚方式**：`git revert` Phase 5 两批提交；L7 归属表可机验。
- **P7-1 补充**：`api.py` 内两段 `return` 后**不可达**的旧实现文本（异常处理器 /
  安全头，真实逻辑在 `routers.common._http_exception_handler_impl` /
  `_add_security_headers`）已删除——行为零变更，纯死代码。

### BC-03 · 共享状态收口为带锁 API（Phase 1 / Phase 6 第 2 批）

- **变更点**：`_scan_tasks` / `_perf_metrics` 裸 dict 访问 → `start/finish/fail/get_task`
  与 `record_metric/flush_metrics`（RLock）。
- **影响面**：内部（routers 域内曾直接下标读写的 6 处）。
- **Before**：`_scan_tasks[key] = {...}`（竞态）。
- **After**：`start_scan(...)` / `get_task(key)`（返回快照）。
- **迁移步骤**：无外部迁移；AST 守卫（`tests/test_phase6_batch2.py`）禁止回退。
- **生效版本**：Phase 1；Phase 6 加锁（ADR-0007 D4）。
- **回滚方式**：不适用（修复性质）。

### BC-04 · 回测引擎统一到 `backtest/` 包（Phase 3）

- **变更点**：`backtest/engine.py` 为唯一引擎；`backtest/unified.py` 6 mode 统一入口；
  结果统一 `BacktestResult` dataclass（`backtest/types.py`）。
- **影响面**：内部脚本 / 测试；`backtest/full.py`、`backtest/rebalance.py` 保留为
  deprecated wrapper（emit DeprecationWarning，内部转调 engine）。
- **Before**：`backtest_full.py` / `backtest_rebalance.py` / `backtest_unified.py`
  三套独立实现。
- **After**：`backtest.unified.BacktestRunner` + 6 mode（L4 5 链路口径基线锁定）。
- **迁移步骤**：脚本改 `from backtest.unified import BacktestRunner`；
  结果字段用 `BacktestResult`。
- **生效版本**：Phase 3（TODO P1，2026-05-31 完成 + RF-01 口径基线）。
- **回滚方式**：wrapper 层可指回旧实现（计划 §三 Phase 3 回滚策略）。
- **P7-2 待办**：wrapper 引用计数仍 >0（unified/engine/__init__），收敛需单独批次；
  `backtest/core.py` 顶部 deprecation 标注**与事实不符**（`run_factor_backtest` 被
  `/api/backtest` 活跃使用），P7-2 改标注而非删模块。

### BC-05 · 评分统一到 ScoringKernel + 灰度开关（Phase 4）

- **变更点**：7 模块 / 9 入口评分经 `lib/scoring/scoring_gateway.py::score_4d` →
  `ScoringKernel.score(source, inputs)` → 纯转发适配器 → 旧实现。
  `FEATURE_UNIFIED_SCORING` 环境变量灰度（默认 off；`all` 或消费者名启用）。
- **影响面**：数值**零变化**（L5：9 入口 × 285 样本容差 1e-9；L6：三分支 off/on 逐字段
  相等）。影响的是调用路径与返回类型（on 侧返回 `ScoringResult`，`raw` 为旧实现原对象）。
- **Before**：`from lib.scoring.scorer import comprehensive_score; comprehensive_score(...)`
- **After**：`from lib.scoring.scoring_gateway import score_4d; score_4d("cli", ...)` /
  消费者经 `unified_scoring_enabled(consumer)` 自动灰度。
- **迁移步骤**：改经 `score_4d`；不要直接调 `scoring_adapters._call_*`（AST 守卫禁止
  适配器内算术——算法只允许在旧实现）。
- **生效版本**：Phase 4（ADR-0001 等）；消费者切换 Phase 4 阶段 B 收官。
- **回滚方式**：删 `FEATURE_UNIFIED_SCORING` 环境变量 = 回旧路径（默认 off）。
- **P7-3 待办**：灰度收口（删 flag 或翻转默认）需单独 ADR——收口后 L6 的 off/on
  对拍退化为空判据（P-54），须同步退役/改判 L6。

### BC-06 · 异步 offload 入口（Phase 6）

- **变更点**：38 个端点的阻塞段经 `lib/infra/async_offload.py::run_blocking(consumer, fn, ...)`
  进线程池；`FEATURE_ASYNC_OFFLOAD`（默认 off；`all` 或消费者名启用）。
- **影响面**：响应**逐字节零差异**（L9 37 迁移 AST 等价 + off/on 0 diff；L10 37 端点
  P95 全改善）。影响的是执行线程：on 侧 fn 在工作线程执行（线程本地状态 / 共享可变
  状态须线程安全——已由 L8/L9/竞态取证覆盖）。
- **Before**：`async def endpoint(...): <阻塞段>`。
- **After**：`async def endpoint(...): return await run_blocking("consumer", _compute_x, ...)`；
  `_compute_x` 为原 try 体逐字同步函数。
- **迁移步骤**：新增端点如含阻塞段，参照 `routers/market.py::_compute_kline` 模式；
  消费者名必须登记 `OFFLOAD_CONSUMERS` 并补 L9/L10 取证（RULES §1.7 T1–T6）。
- **生效版本**：Phase 6（ADR-0007/0010/0011/0012/0013）。
- **回滚方式**：`FEATURE_ASYNC_OFFLOAD=0`（或删环境变量）= 同步直调（默认）。
  **开关是运行时回滚能力，永久保留。**

### BC-07 · 预存在坏端点（发现于 Phase 6 第 7 批，非 RF-01 引入）

- **变更点**：无（本条是发现记录，修复归 RF-02）。
- **影响面**：生产 API 消费方（前端）。
- **事实**：`GET /api/notifications`、`GET /api/notifications/stats`（模块缺
  `get_notifications` → AttributeError）与 `GET /api/strategy/recommend`（模块缺
  `FactorOptimizer` → ImportError）真实运行时**必然走空兜底**；相关结构守卫
  「名字出现即通过」长期全绿（坑表 P-75）。
- **迁移步骤**：修复归 RF-02（会改变响应内容：从空兜底变为真实数据，属 BC-第 1 类
  「响应字段语义变化」，须独立 fix 批次 + 契约快照对照 + ADR）。
- **生效版本**：待 RF-02。
- **回滚方式**：不适用。

### BC-08 · 评分灰度收口（Phase 7 P7-3，ADR-0014）

- **变更点**：`FEATURE_UNIFIED_SCORING` 环境变量与 `config.unified_scoring_enabled()`
  删除；`scoring_gateway.score_4d(fundamental, capital_flow, news, technical)` 去掉
  `consumer` 参数，恒经 `ScoringKernel`；`GRAY_CONSUMERS` / `score_4d_result` 删除；
  L6 对拍脚本退役。
- **影响面**：消费者可见返回值**零变化**（两分支本就返回旧实现原对象 `.raw`）；
  仓内 3 个调用方（strategies / steps / run.py）已同批更新；无外部消费方。
- **Before**：`score_4d("cli", f, c, n, t)`；`FEATURE_UNIFIED_SCORING=all` 切内核路径。
- **After**：`score_4d(f, c, n, t)`（唯一路径 = kernel）；无数值切换手段。
- **迁移步骤**：改 import 调用签名即可；数值口径由 L5 基线锁定（285 样本容差 1e-9）。
- **生效版本**：Phase 7 P7-3（2026-11-12）。
- **回滚方式**：`git revert` P7-3 提交（flag 机制整体恢复）。

### BC-08b · `notifier.py` 下线 → `reporter/scan_notify.py`（Phase 7 P7-2）

- **变更点**：根目录弃用壳 `notifier.py`（自 2026-05-31 起 emit DeprecationWarning）
  删除；4 个活函数（`get_tenant_token` / `format_stock_table` /
  `build_message_content` / `send_feishu_message` / `print_results`）**逐字**迁入
  `reporter/scan_notify.py`。
- **影响面**：仓内 2 个调用方（`run.py:10`、`lib/strategy/pipeline.py:425`）已同批改
  import；无外部消费方。
- **Before**：`from notifier import send_feishu_message, print_results`
- **After**：`from reporter.scan_notify import send_feishu_message, print_results`
- **payload 对拍**：旧（`git show HEAD:notifier.py`）vs 新 `build_message_content`
  固定输入（patched sentiment / frozen datetime）**逐字节一致**；HTTP 发送路径逐字相同。
  另：原模块未使用的 `get_realtime_quotes` import 未随迁（搬迁前即未使用）。
- **迁移步骤**：改 import 行即可；函数签名与返回值不变。
- **生效版本**：Phase 7 P7-2（2026-11-12）。
- **回滚方式**：`git revert` P7-2 提交。
- **注意**：`reporter.feishu_pusher` 是**另一条独立推送管线**（token 缓存 / 图片上传 /
  push_log / 降级打印），本迁移未合并二者——合并会改变推送行为，如需合并须单独 ADR。

### BC-09 · `judge_panel` 死方法删除（Phase 7 P7-2）

- **变更点**：`_evaluate_value` / `_evaluate_growth` / `_evaluate_macro` /
  `_evaluate_technical` / `_evaluate_cn_value` / `_evaluate_hot_money` 6 个方法
  （150+ if/elif 硬编码规则，679→224 行）与 `_fund_data_missing` 删除；
  顶层未用 import（numpy / Optional / field）清理。
- **影响面**：无生产调用方（自 P0#13 / commit 938933d 起 `evaluate_all()` 不再调它们，
  全仓 grep 调用点 = 0）；钉死私有方法的 5 个测试用例随删。
  **`JUDGE_GROUPS` / `_DEFAULT_JUDGE_GROUPS` / `evaluate_all` /
  `evaluate_with_ml` / `_evaluate_ml` / `get_summary` / `_get_judge_weight` 全部保留**，
  L5 评分基线（285 样本）逐字段不变。
- **Before / After**：无调用方式变化；`JudgePanel` 对外行为（`evaluate_with_ml` 返回值）
  逐字节不变。
- **迁移步骤**：无（死代码）。若未来要复活硬编码规则组，从 git 历史
  （P7-2 前的提交）恢复，且必须先走 walk-forward 验证（P0#13 的原始结论）。
- **生效版本**：Phase 7 P7-2（2026-11-12）。
- **回滚方式**：`git revert` P7-2 提交。

---

## 三、Phase 7 路线图（2026-11-12 起）

| 批次 | 内容 | 状态 |
|---|---|---|
| **P7-1** | MIGRATION-GUIDE 定稿（本文件）；删除 `factors.detect_market_regime` + `MarketRegime`（全仓 0 引用）；`api.py` 不可达死代码；`pipeline.py` 未用 import（`comprehensive_score`） | ✅ 2026-11-12 |
| **P7-2** | `notifier.py` 弃用壳下线（活函数逐字迁 `reporter/scan_notify.py`，2 个调用方改 import，payload 对拍逐字节一致）；`judge_panel.py` 6 个 `_evaluate_*` 死方法 + `_fund_data_missing` 删除（679→224 行，L5 基线不变）；`backtest/core.py` 误导性 deprecation 标注改正（import 警告 1→0；`run_factor_backtest` 是活实现）。**收敛评估结论**：`backtest/full.py` / `rebalance.py` wrapper 是包公共面（`__init__` 再导出 + 测试 patch 目标）且警告属实，**保留**，引用收敛推迟到有真实收益时 | ✅ 2026-11-12 |
| **P7-2b** | judge_panel 清理时发现 `config/judges.json` 的 6 组权重非零但对应投票路径已死（`evaluate_all` 不再产生规则票）——**配置与实现漂移**，随 RF-02 一并处置（删 JSON 条目或复活规则组，须 walk-forward 验证） | 待推（登记） |
| **P7-3** | scoring 灰度收口（**用户拍板：删 flag**）：`score_4d` 去 consumer 参/去分支（两侧本就返回旧实现原对象 `.raw`，收口对消费者不可见）；`GRAY_CONSUMERS` / `score_4d_result`（零调用死 API）/ `scoring_contract.unified_scoring_enabled` / `config.unified_scoring_enabled` 删除；L6 脚本退役（P-54 空判据），L5 继续锁数值口径；3 个消费者调用点同批更新 | ✅ 2026-11-12 |
| **P7-4** | 配置集中化收尾：env 读取去重（TDX 系 8 处 / FALLBACK_DIR / TDX_REF_PATH 归 config；API_SECRET_KEY / TUSHARE_TOKEN / STOCK_FONT_PATH 改 config accessor 调用时读取保 monkeypatch 语义）+ `TRADING_DAYS_PER_YEAR` 单一化（4 处重复定义删除 + 3 处裸 252 年化换常量）。**判据修正**：计划原口径「31 处硬编码周期常量并入 config.py」实测正则已漂移到 160 处，其中绝大多数为函数签名默认值 / 算法局部窗口 / 评分启发式，**不属于配置**——只收敛真正跨模块同义的年化常数，其余保留原地并留痕 | ✅ 2026-11-12 |
| **P7-5** | 文档收口（CLAUDE.md / docs/architecture.md / TODO 关闭 P1/P8 已完成项 + 台账漂移项）；破坏点 ADR 编号连续性核查；推送触发 CI 三平台绿灯（破坏性删除的前置条件 ③） | 待推 |

**范围（不做）**（计划 §三 Phase 7）：不删除任何被引用的旧符号——`scoring_adapters.py`
纯转发层是 `ScoringKernel` 的调用骨架（"本文件被删掉也不会影响任何评分结果"指数值，
不是指可删）；旧评分实现（`scorer.py` 等）是唯一算法来源，**保留**。

---

## 四、残留扫描方法与证据（P7-1）

方法：对每个被删符号全仓 grep（排除 `docs/`、`archive/`、`node_modules/`、本指南）。

```bash
# BC-01 被删符号：期望 0 命中（factors.py 自身亦无）
grep -rn "detect_market_regime" --include="*.py" . | grep -v "lib/monitoring/market_regime\|docs/\|archive/\|node_modules\|MIGRATION-GUIDE\|test_market_regime"
#   → 剩余命中全部是 lib.monitoring 新来源的调用方（backtest/engine、strategies、pipeline、
#     routers/market、backtest/core、dashboard、run_v2）或 portfolio_optimizer 自有同名方法；
#     无任何 `from factors import detect_market_regime` / `factors.detect_market_regime` ✅
grep -rn "factors.MarketRegime\|from factors import.*MarketRegime" --include="*.py" .
#   → 0 命中 ✅（2026-11-12 P7-1 删除后复核）

# BC-02 被删死代码：api.py 内已无 return 后语句（pyflakes/AST 复核）
python -c "import ast; ast.parse(open('api.py', encoding='utf-8').read())"  # ✅

# pipeline.py 未用 import：comprehensive_score 全文件仅 import 行
grep -n "comprehensive_score" lib/strategy/pipeline.py   # → 0 命中（删除后）✅
```

删除前引用计数取证（2026-11-12 清点）：
- `factors.detect_market_regime`：全仓 0 调用方（测试 3 个文件均 import
  `lib.monitoring.market_regime`）。
- `factors.MarketRegime`：唯一引用者是上述被删壳。
- `pipeline.py comprehensive_score`：仅 import 行（grep 全文件）。
- `api.py` 不可达段：`return` 之后的语句块（Python 语义上不可达）。

P7-2 残留扫描证据（2026-11-12）：
- `notifier.py`：删除后全仓 `from notifier import | import notifier` 命中 = **0**。
- judge_panel 6 个 `_evaluate_*` + `_fund_data_missing`：删除前全仓调用点 = **0**
  （`test_judge_panel.py` 只调 `evaluate_all` / `evaluate_with_ml` / `_evaluate_ml` /
  `_get_judge_weight` / 私有 `_fund_data_missing`——后者 5 个用例随删）。
- payload 对拍：旧 notifier（`git show HEAD:notifier.py`）vs `reporter.scan_notify`
  的 `build_message_content`，固定输入逐字节一致（`get_market_sentiment` 与
  `datetime.now` 双侧同 mock）。

P7-3 残留扫描证据（2026-11-12）：
- 全仓 `FEATURE_UNIFIED_SCORING | unified_scoring_enabled`：lib/scripts/config/run.py 命中
  = **0**（仅注释性历史说明与测试禁止名单 `SCORING_SYMBOLS`——后者的作用是守卫回退）。
- L5 评分基线 285 样本逐字段不变；L1 基线 1293 passed / 12 skipped
  （-14 逐条可解释：switch 净 -7 + contract -7）。

P7-4 残留扫描证据（2026-11-12）：
- lib/ 内 `os.getenv | os.environ.get` 从 15 处收敛到 **2 处受控调用时重读**
  （`market_utils._tdx_root()`、`data_layer` 内 `tdx_path` 读取——均为保测试
  monkeypatch 语义的既定约定，默认值取自 config）；其余全部归 config.py 所有。
- `TRADING_DAYS_PER_YEAR = 252` 定义从 4 处收敛到 config.py 1 处；
  裸 `252` 年化字面量清理 4 处（analyzer 1 + portfolio_optimizer 3）。
- 读取时机语义保持：模块级读取（TDX 系）仍为 import 时；调用时读取
  （API_SECRET_KEY / TUSHARE_TOKEN / STOCK_FONT_PATH）经 config accessor 仍在调用时
  执行——`test_api.py`（monkeypatch.setenv API_SECRET_KEY）、`test_pdf_font.py`、
  `test_market_utils.py`（37 例 monkeypatch TDX_DATA_PATH）全部原样通过。

---

## 五、回滚方式汇总

| 变更 | 回滚 |
|---|---|
| BC-01 / BC-02（P7-1 删除） | `git revert` P7-1 提交 |
| BC-03 | 不适用（修复） |
| BC-04 | wrapper 指回旧实现（Phase 3 回滚策略） |
| BC-05 | 删 `FEATURE_UNIFIED_SCORING` 环境变量（默认 off = 旧路径） |
| BC-06 | 删 `FEATURE_ASYNC_OFFLOAD` 环境变量（默认 off = 同步直调） |
| 全局 | `docs/refactor/contracts/*baseline.json` 全部可 `--capture` 重建；十把尺子 `--check` 可复算 |
