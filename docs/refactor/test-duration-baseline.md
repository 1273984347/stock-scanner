# 本地 pytest 耗时基线与稳定性基线

> 工具：`scripts/pytest_duration_baseline.py` ｜ 数据：`test-duration-baseline.json`
> 环境：Python 3.12.10 · pytest 8.4.2 · win32 · 2026-11-07 05:0x–05:2x
> 关联：RF-01 Phase 0 剩余项 #7（本地慢用例定位）

---

## 一、要解决的问题与最终结论

| 口径 | 读数 |
|---|---|
| 本地全量 pytest（**ini 默认，开实时 DEBUG 日志**） | **> 28 min 未结束**（两次采集均在 13% 处中止） |
| 本地全量 pytest（**关实时日志**，测量口径） | **635.37s ≈ 10.6 min**（111 个测试文件逐文件实测之和） |
| CI（`ubuntu-latest, 3.12`，ini 默认开日志） | **110.69s**（1,117 passed + 3 failed + 12 skipped） |
| **✅ 治理后（默认禁网 fixture，见 §十）** | **69.3s**（ini 默认开日志）/ **63.07s**（关日志）⇒ 相对治理前 **>24× / 10.1×**；**零新增失败** |
| **日志 I/O 放大占比** | **约 62%**（28min → 10.6min） |
| **剩余真实差距** | **约 5.7 倍**（635s vs 110.69s） |

**结论**：本地慢有两个叠加因素，且**主因不是机器**：

1. **放大器**：`pytest.ini` 的 `log_cli=true` + `log_cli_level=DEBUG`，叠加 `tests/logging.conf` 的
   `logger_quant`(level=DEBUG, handler→stdout) ⇒ **DEBUG 日志双份打到终端**，
   matplotlib 的 `font_manager` 对每个字体逐条打分被逐条放大。
2. **真因**：**单个测试文件 `tests/test_scorecard.py` 就要 304.65s（占本地总时长 47.9%）**，
   其余差距来自 5 个同类文件。根因是**测试中存在未隔离的真实网络路径**（详见 §五）。

---

## 二、测量方法（不这么做测出来的"慢"是假的）

| 做法 | 原因 |
|---|---|
| **一律关闭实时日志** `-o log_cli=false -o log_cli_level=WARNING` | 见 §一 放大器；不关日志测出来的"慢"大半是日志而非被测代码 |
| 用 `--durations=0` 取全部用例耗时（而非默认 top-N） | 需要完整排序，不能只取头部 |
| 逐文件跑（`--scan-by-file`）而非只跑全量 | 全量只能给"总时长"，无法归因到文件；逐文件跑才能把慢区收敛到具体文件 |
| 解析 durations 块标题时**兼容两种形态** | 实测 pytest 8.4.2：`--durations=0` → `slowest durations`；`--durations=60` → `slowest 60 durations`。写死任一种都会在另一种下解析出 **0 条**（本工具连踩两次） |

---

## 三、耗时排序 · 文件级（111 个测试文件全覆盖，总 635.37s）

| # | 文件 | 耗时 | 占比 |
|---:|---|---:|---:|
| 1 | `tests/test_scorecard.py` | **304.65s** | **47.9%** |
| 2 | `tests/test_pipeline_core_steps.py` | **100.50s** | **15.8%** |
| 3 | `tests/test_batch_quick_score.py` | **41.38s** | **6.5%** |
| 4 | `tests/test_strategies.py` | **32.85s** | **5.2%** |
| 5 | `tests/test_strategies_p1_1.py` | **24.13s** | **3.8%** |
| 6 | `tests/test_datalayer_rate_limit.py` | **17.81s** | **2.8%** |
| 7 | `tests/test_new_modules.py` | 8.21s | 1.3% |
| 8 | `tests/test_import_all.py` | 4.16s | 0.7% |
| 9 | `tests/test_backtest_charts_cleanup.py` | 3.38s | 0.5% |
| 10 | `tests/test_api.py` | 3.05s | 0.5% |
| 11 | `tests/test_smoke.py` | 2.94s | 0.5% |
| 12 | `tests/test_backtest_risk_integration.py` | 2.50s | 0.4% |

**Top 12 个文件 = 85.9% 的总耗时**；其余 99 个文件合计仅 ~91s。
⇒ **修掉前 6 个文件，本地全量可从 10.6min 降到 ~2min。**

---

## 四、耗时排序 · 用例级（Top 15）

| # | 用例 | 耗时 | 占比 |
|---:|---|---:|---:|
| 1 | `tests/test_scorecard.py::test_score_structure` | **188.07s** | **29.6%** |
| 2 | `tests/test_scorecard.py::test_score_empty_hist` | **115.84s** | **18.2%** |
| 3 | `tests/test_pipeline_core_steps.py::test_fetch_stock_data_handles_empty` | **96.43s** | **15.2%** |
| 4 | `tests/test_batch_quick_score.py::test_batch_quick_score_basic_parallel` | 29.58s | 4.7% |
| 5 | `tests/test_strategies_p1_1.py::test_p1_1_full_score_runs_without_name_error` | 23.38s | 3.7% |
| 6 | `tests/test_datalayer_rate_limit.py::test_get_realtime_quotes_handles_akshare_rate_limit` | 17.06s | 2.7% |
| 7 | `tests/test_new_modules.py::TestMarginData::test_get_margin_indicator_structure` | 7.05s | 1.1% |
| 8 | `tests/test_batch_quick_score.py::test_batch_quick_score_handles_exception_in_one_row` | 6.05s | 1.0% |
| 9 | `tests/test_batch_quick_score.py::test_batch_quick_score_skip_rows_without_code` | 4.84s | 0.8% |
| 10 | `tests/test_strategies.py::TestFullScore::test_full_score_grading_boundary` | 3.06s | 0.5% |
| 11 | `tests/test_strategies.py::TestFullScore::test_full_score_returns_structure` | 2.99s | 0.5% |
| 12 | `tests/test_strategies.py::TestQuickScore::test_quick_score_with_minimal_data` | 2.01s | 0.3% |
| 13 | `tests/test_pipeline_core_steps.py::test_score_without_realtime_returns_dict` | 2.00s | 0.3% |
| 14 | `tests/test_smoke.py::test_scanner_import` | 1.49s | 0.2% |
| 15 | `tests/test_alpha_factory.py::TestAlphaFactory::test_discover_with_low_ic_rejected` | 1.47s | 0.2% |

**Top 2 用例（都在 `test_scorecard.py`）= 303.91s = 总时长的 47.8%。**

---

## 五、稳定性基线

> 每轮独立起 pytest 进程跑同一组用例。**CV = std / mean**，CV > 0.3 判定为不稳定。
> 数据源：`test-duration-baseline.json` 的 `stability` 段（可 diff 两次运行）。

### 5.1 四个关键结论

**① 「稳定」不等于「正常」。**
CV **0.0038**、max/min **1.01** 的用例稳定地要 17.08s —— 因为它的 sleep 是**确定性退避**
（`_retry_api_call` 的 2s→4s backoff），不是网络抖动 ⇒ 这是**可预测的固定成本**，修完即消失。
而 max/min **1.42~1.43** 的用例是**真实网络抖动** ⇒ 环境相关，**不能当性能基准**。
⇒ 稳定性基线必须同时给 **median 与抖动幅度**，只看均值会把这两类混为一谈。

**② 耗时依赖执行顺序，不能直接当性能基准。**
同一个用例：与其余 205 个用例同批跑时 **39.99s**，小批单独跑时 median **92.01s**（2.3 倍）
⇒ 前序用例填充的 cache 让后续命中缓存。**这类用例在 CI 与本地会呈现完全不同的耗时**，
拿它做性能回归基准会得到假信号。

**③ 慢 ≠ 不稳定；快也不等于安全。**
本地总时长 47.9% 来自 2 个用例，而它们的稳定性取决于网络状态 ⇒
**先修「未隔离的外部依赖」，再谈性能基准**，顺序反了会白做。

**④ CV 在 2–3 轮的小样本下不可靠，须用 max/min 比值兜底。**
同一用例 `test_fetch_stock_data_handles_empty`：3 轮测得 CV **0.3018**（判红），
2 轮测得 CV **0.174**（不判红）⇒ **样本量不足以稳定判定 CV**。
max/min 比值不依赖方差估计，是更稳健的抖动指标：
本基线中 `test_get_realtime_quotes_handles_akshare_rate_limit` = **1.01**（确定性退避）
vs `test_fetch_stock_data_handles_empty` = **1.42**、`test_batch_quick_score_basic_parallel` = **1.43**（真实网络抖动）。
⇒ **判定建议**：`max/min > 1.3` 视为环境相关（不可作基准），`≤ 1.1` 视为确定性固定成本（修完即消失）。

### 5.2 已测条目（按 median 降序）

| 用例 | runs | min | median | max | mean | std | **CV** | **max/min** | 判定 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:---|
| `test_scorecard.py::test_score_structure` | 2 | 192.08 | **214.39** | 236.69 | 214.39 | 22.31 | 0.104 | 1.23 | 🟡 头号慢源 |
| `test_scorecard.py::test_score_empty_hist` | 2 | 108.65 | **111.30** | 113.94 | 111.30 | 2.65 | 0.0238 | **1.05** | 🟢 稳定但慢 |
| `test_pipeline_core_steps.py::test_fetch_stock_data_handles_empty` | 2 | 76.79 | 92.97 | 109.14 | 92.97 | 16.18 | 0.174 | **1.42** | 🔴 环境相关 |
| `test_batch_quick_score.py::test_batch_quick_score_basic_parallel` | 3 | 28.42 | 28.58 | 40.72 | 32.57 | 5.76 | 0.1769 | **1.43** | 🔴 环境相关 |
| `test_datalayer_rate_limit.py::test_get_realtime_quotes_handles_akshare_rate_limit` | 3 | 17.01 | 17.08 | 17.17 | 17.09 | 0.07 | 0.0038 | **1.01** | 🟢 稳定但慢 |
| `test_new_modules.py::TestMarginData::test_get_margin_indicator_structure` | 3 | 6.32 | 6.65 | 7.26 | 6.74 | 0.39 | 0.0577 | 1.15 | 🟢 |
| `test_batch_quick_score.py::test_batch_quick_score_skip_rows_without_code` | 3 | 5.73 | 5.84 | 5.94 | 5.84 | 0.09 | 0.0147 | 1.04 | 🟢 |
| `test_batch_quick_score.py::test_batch_quick_score_handles_exception_in_one_row` | 3 | 4.77 | 4.79 | 5.03 | 4.86 | 0.12 | 0.0243 | 1.05 | 🟢 |
| `test_pipeline_core_steps.py::test_score_without_realtime_returns_dict` | 3 | 3.01 | 3.07 | 3.23 | 3.10 | 0.09 | 0.0299 | 1.07 | 🟢 |
| `test_api.py::TestStockCodeValidation::test_valid_6digit_code` | 3 | 0.10 | 0.10 | 0.11 | 0.10 | 0.01 | 0.0456 | 1.10 | 🟢 |

**轮次 wall 时间**：
- Top-8 组（3 轮）：170.04s → 161.61s → 233.02s（**整体波动 44%**）
- Top-3 组（2 轮）：380.73s → 463.02s（**波动 21.7%**）

> 注：`test_fetch_stock_data_handles_empty` 曾以 3 轮测得 CV 0.3018（max 163.02s / min 87.85s），
> 后以 2 轮复测得 CV 0.174（max 109.14s / min 76.79s）⇒ **它的不稳定性是真实的，但 2–3 轮样本不足以量化**，
> 应以 max/min **1.42** 作为判据。

---

## 六、根因：三类「未隔离的真实网络路径」

| 模式 | 说明 | 证据 |
|---|---|---|
| **① mock 不全** | 测试 patch 了被测函数的几个直接依赖，但函数内部还有未 patch 的网络调用 | `test_fetch_stock_data_handles_empty` patch 了 `get_stock_hist_multi_source` / `get_realtime_quotes` / `_get_financial_data` / `get_index_hist`，**仍有漏网调用**。同文件注释自认："get_stock_score 需要 network, 这里只验证 import OK" |
| **② 显式走真实逻辑** | 注释明确声明不走 mock | `test_batch_quick_score_basic_parallel` 注释："**无 mock 真实 quick_score 走完整逻辑**"。文件头只设了 `NO_VERIFY_TDX=1`（跳过 TDX 校验，**不阻止网络**） |
| **③ mock 了错误但没 mock 退避** | patch 让函数抛错触发重试；但**退避常量没 patch** ⇒ 真的 sleep | `test_get_realtime_quotes_handles_akshare_rate_limit` 触发的 `lib/data_layer._retry_api_call`：限流错误 backoff 翻倍（`API_RETRY_BASE_WAIT_S=2.0` → 2s、4s），叠加 `_rate_limiter` 的 3 calls/s 限流等待。同文件其它测试**已正确 patch** 了 `API_RETRY_BASE_WAIT_S` → 0.01，说明这条是遗漏 |

**头号慢源 `tests/test_scorecard.py`（304.65s）属于哪一类？**
`lib/scorecard.py` 正是 `tests/conftest.py` 注释里点名的三个元凶之一
（"lib/data_ashare.py + lib/data_layer.py + **lib/scorecard.py** 等调 akshare / sina / eastmoney 真实网络 API"），
而 `conftest` 的 autouse fixture **只 mock 了 akshare 的 7 个接口**，
`sina` / `eastmoney` 与其余 akshare 接口仍然漏网 ⇒ 属于 **① mock 不全**（且被 2026-06-06 那次修复漏掉）。

### 实测单次未 mock 接口耗时

| 接口 | 耗时 |
|---|---:|
| `ak.stock_zh_a_hist`（**最常用，且未被 mock**） | **1.53s** |
| `ak.stock_board_industry_name_em`（未被 mock） | **13.14s** |
| sina hq（`requests`，未 mock） | **超时 6.75s** → 再经退避 2s+4s ⇒ 单次最坏 **~12.75s** |
| `ak.stock_zh_a_spot`（已 mock） | 真实调用需 13.53s（70 页分页） |

### 为什么本地慢、CI 快

本地出网顺畅 ⇒ 未隔离的调用**走完全程**（1.5s~13s 的真实请求 + 退避 sleep）；
CI runner 上这些调用**快速失败** ⇒ 表现为「失败但快」。
⇒ **同一份测试集在两个环境测出完全不同的耗时**，这是 "CI 110s vs 本地 10.6min" 的直接成因。

---

## 七、方法论自省：漏斗策略漏掉了头号慢源

第一轮用「静态 grep 圈定候选」定位到 23 个文件，**但漏掉了 4 个慢文件**：

| 漏掉的慢文件 | 耗时 | 漏掉的原因 |
|---|---:|---|
| `tests/test_scorecard.py` | **304.65s** | 文件内**不直接出现** `sleep`/`requests`/`akshare` 等关键词 —— 它只是**间接**调用 `lib/scorecard.py` |
| `tests/test_strategies.py` | 32.85s | 同上（间接调用） |
| `tests/test_strategies_p1_1.py` | 24.13s | 同上 |
| `tests/test_import_all.py` | 4.16s | 同上 |

⇒ **静态 grep 只能发现"直接"外部调用者，查不出间接调用者**。
若当时直接拿 23 个文件的结论收工，会**漏掉占本地总时长 47.9% 的头号慢源**。
⇒ 教训：**漏斗的最后一步必须是全覆盖实测**，不能只靠静态侦察圈定范围。

---

## 八、复算方式

```powershell
# 1) 候选文件耗时排序（快速定位慢区）
python scripts/pytest_duration_baseline.py --collect --files tests/test_scorecard.py,tests/test_pipeline_core_steps.py --top 40 --out docs/refactor/test-duration-baseline.json

# 2) 全测试文件级扫描（覆盖全部 tests/test_*.py，本基线即此产物）
python scripts/pytest_duration_baseline.py --scan-by-file --out docs/refactor/test-duration-baseline.json

# 3) 稳定性基线（对最慢的 N 个用例重复 R 轮；按 nodeid 合并，不覆盖历史条目）
python scripts/pytest_duration_baseline.py --verify --repeat 3 --top 8 --out docs/refactor/test-duration-baseline.json
```

**判据**：`CV > 0.3` 判定为不稳定用例；稳定基线以 JSON 为准（可 diff 两次运行的差异）。

---

## 九、建议（本次未实施，仅登记）

按性价比排序，供 Phase 0 后续或独立立项使用：

| 优先级 | 动作 | 预期收益 |
|:---:|---|---|
| 1 | 给 `tests/conftest.py` 的 autouse fixture 加**默认禁止 socket**（monkeypatch `socket.socket.connect` 抛错），保留 `enable_socket` 标记豁免 | 一举封死模式 ①②③，本地全量从 10.6min 回到 CI 量级（~2min） |
| 2 | `test_scorecard.py` 补 mock（`lib/scorecard.py` 的 akshare/sina/eastmoney 入口） | **304.65s → <1s**（占总时长 47.9%） |
| 3 | `test_get_realtime_quotes_handles_akshare_rate_limit` 的退避常量 patch 成 0.01（同文件其它测试已这么做） | 17.06s → < 0.1s |
| 4 | `test_batch_quick_score_*` 补 mock（把"无 mock 走完整逻辑"改为 mock 数据源） | 三条合计 39.7s → < 1s |
| 5 | `pytest.ini` 的 `log_cli_level` 默认降到 `WARNING`（需要时用 `-o log_cli=true` 临时开） | 消除日志 I/O 放大（实测占 62%） |
| 6 | `test_fetch_stock_data_handles_empty` 补齐 `steps_mod` 的全部网络入口 patch | 96.43s → < 0.1s |

> 说明：第 1 项与 `conftest.py` 现有注释「网络隔离（修 hang 35+ min 元凶 2026-06-06）」是同一条路的延续 ——
> 上次只挡了 akshare 的 7 个接口，**其余数据源与未 mock 的 akshare 接口仍然漏网**，
> 而 `lib/scorecard.py` 正是在那次注释里被点名的三个元凶之一。
>
> ✅ **第 1 项已于 2026-11-07 落地**，实测效果见 §十（全量 635.37s → **63.07s**，零新增失败）。

---

## 十、治理落地：默认禁网 autouse fixture（2026-11-07）

### 10.1 实施

`tests/conftest.py` 新增 autouse fixture `_block_network_by_default`：

| 项 | 内容 |
|---|---|
| 阻断 | `socket.socket.connect` / `connect_ex` / `socket.create_connection` / `socket.getaddrinfo` 的**非本地**地址 |
| 放行 | **loopback**（`127.0.0.1` / `::1` / `localhost`）与 **unix socket path**（str） |
| 豁免 | `@pytest.mark.enable_socket`（沿用既有标记机制；当前无测试使用） |
| 异常类型 | `NetworkBlockedError(ConnectionError)` —— 与 requests/urllib3 对 socket `OSError` 的包装同族，使下游「断网降级」分支与真实断网行为一致 |

### 10.2 效果

| 口径 | 治理前 | 治理后 | 改善 |
|---|---:|---:|---:|
| **全量 pytest（ini 默认，开实时 DEBUG 日志）** | **> 28 min** | **69.3s** | **> 24×** |
| **全量 pytest（关实时日志）** | 635.37s | **63.07s** | **10.1×** |
| 6 个最慢文件（合） | 521.32s | 54.13s | 9.6× |
| `test_scorecard.py::test_score_structure` | 188.07s | **0.04s** | ~4700× |
| `test_pipeline_core_steps.py::test_fetch_stock_data_handles_empty` | 96.43s | <0.01s | — |
| **失败清单** | 34（本地既有） | **34（同一批）** | **零新增** |

全量结果：`1,085 passed, 25 failed, 13 skipped, 9 errors in 63.07s`。
（CI 参考：`ubuntu-latest, 3.12` 上 110.69s / 3 failed / 12 skipped）

**日志放大同时塌陷**：治理后开日志 69.3s vs 关日志 63.07s ⇒ 放大从 **62% 降到约 10%**
（日志文件仅 0.9 MB）。原因：**放大主源就是网络调用产生的 DEBUG 日志** ——
调用没了，日志也没了。⇒ 这也反证了 §一 的归因：本地慢的根因是未隔离的真实网络路径，
日志放大只是它的**放大器**，不是独立问题。

### 10.3 失败归属的对照实验（方法值得复用）

**不能只靠"新失败数 = 旧失败数"下结论** —— 必须做同口径对照：

```powershell
git stash push -- tests/conftest.py          # 回退 fixture
python -m pytest <同一批文件> -q --tb=no -o log_cli=false   # 对照跑
git stash pop                              # 还原
```

实测：对照跑（无 fixture）同样得到 **25 failed + 9 errors**，且是**完全相同的 34 个用例**，
全部为 `os.replace` 的 `PermissionError`（Windows 文件锁 / portalocker 语义），
与 socket 无关 ⇒ **禁网 fixture 零新增失败**。
（对照跑仅 1.83s，也侧面证明这批失败与网络无关。）

### 10.4 fixture 自己踩的坑：不能无差别挡 socket

第一版 fixture 拦掉**所有** `socket.socket.connect`，导致 **13 个 `test_api.py` 用例全红**
（另 25 failed / 9 errors 里也混入了它们）。

**根因**：**Windows 上 asyncio 的 `socket.socketpair()` 是用真实 `connect()` 到 127.0.0.1 模拟实现的**
（`lsock.bind()` + `csock.connect()`）⇒ 被拦 ⇒ event loop 创建失败：

```
AttributeError: 'ProactorEventLoop' object has no attribute '_ssock'
```

⇒ 连带**所有用 FastAPI/httpx TestClient 的测试全挂**（TestClient 依赖 event loop）。

**修法**：放行 loopback 与 unix socket path（见 §10.1 表）。
Linux/macOS 上 multiprocessing 的 socketpair 走 AF_UNIX（str path）⇒ 同样必须放行。
⇒ **推广：凡做「默认禁网」的测试 fixture，必须放行 loopback 与 AF_UNIX，
否则会打断 asyncio / multiprocessing 的内部机制 —— 且这类失败只在 Windows 暴露。**

### 10.5 禁网解决不了的残余慢点

| 用例 | 耗时 | 性质 | 对应建议 |
|---|---:|---|---|
| `test_get_realtime_quotes_handles_akshare_rate_limit` | 14.66s | **确定性退避**（模式③，与网络无关） | §九 建议 ③：patch 退避常量为 0.01 |
| `test_batch_quick_score_basic_parallel` | 8.98s | 仍需定位（并行 + 其它 IO） | §九 建议 ④ |
| `test_strategies.py` × 4 | 各 3.00s | 整齐的固定成本（非网络） | 待定位 |

⇒ 禁网解决的是 **①② 类（未隔离的真实网络路径）**；**③ 类（确定性退避）与其它固定成本需单独处理**。

