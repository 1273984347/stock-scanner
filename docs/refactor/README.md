# docs/refactor/ —— 重构度量基线（RF-01 / Phase 0）

> 本目录是 RF-01 分阶段重构计划的 **Phase 0「度量基线与安全网」** 产物。
> 目的：在动任何业务代码之前，先把「改动是否改变对外行为」变成**机器可判据**。
> 原则：**没有尺子，「可破坏契约」等于盲改。**
>
> **执行期踩过的坑见 [`pitfalls.md`](pitfalls.md)**（57 条，附证据与推广判据）——
> 改这块的代码前先读它，能省掉本会话里实际付出过的排查成本。
>
> **六把尺子**（改动是否改变对外行为 → 机器可判据）：
> ① 契约快照（路径/schema） ② 覆盖率基线（回归幅度） ③ 双跑对拍（数值）
> ④ **回测口径基线**（回测数值 = 策略决策依据，见 §七）
> ⑤ **评分对拍基线**（评分数值 = 选股信号依据；9 入口 × 285 样本）
> ⑥ **开关响应 diff**（两条评分分支 × 灰度前后整份响应逐字段比对 + 自证内核确实被调用）
>
> **RF-01 进度**：Phase 0（尺子）✅ · Phase 1（公共状态）✅ · Phase 2（数据层）✅ · Phase 3（回测引擎）✅
> · Phase 4 阶段 A ✅（统一契约内核 + 9 适配器 + 评分对拍基线）
> · **Phase 4 阶段 B 进行中**（已切 `strategies` + `pipeline` 两个消费者，`FEATURE_UNIFIED_SCORING` 默认 off）
> → 下一批：`/api/score/{code}` 等端点消费者，见 `TODO.md` 阶段 B 清单
>
> ④ 的 5 条链路分工：`entry_signals`（三份 `check_entry` 判定序列，锁定**口径**）、
> `core_factor_backtest` / `engine_donchian_entry` / `engine_multi_signal_entry`（锁定**引擎产出**）、
> `unified_modes`（打**统一入口** `backtest/unified.py` 的 6 个 mode ——
> **只有它能发现「入口层参数漏传」这类漂移**，底层链路看不到）

---

## 一、目录内容

| 文件 | 作用 | 生成方式 |
|---|---|---|
| `baseline.md` | 规模/覆盖率/端点等**基线读数**台账（各阶段增量对比的参照） | 人工维护 + 脚本回填 |
| `contracts/route-contract-baseline.json` | **路由契约快照**：路径 + 方法 + async/sync 属性 + 鉴权依赖 + 参数 + 响应状态码 | `python scripts/contract_snapshot.py --out docs/refactor/contracts` |
| `contracts/openapi-baseline.json` | FastAPI 标准 OpenAPI 文档（给前端/网关/契约测试比对） | 同上（同一次运行一并产出） |
| `test-duration-baseline.md` | **本地 pytest 耗时排序 + 稳定性基线**（慢用例定位与根因分析） | 人工维护 + 工具产出数据 |
| `test-duration-baseline.json` | 上述基线的机器可读版（耗时排序 / 稳定性 min·median·max·CV） | `scripts/pytest_duration_baseline.py` |
| `pitfalls.md` | **踩坑集**：RF-01 执行期真实踩过的 57 个坑（现象 → 根因 → 修复 → 推广），按度量/跨平台/测试/搬迁/数据/CI/工具链分七类 | 人工维护（每批收官追加） |
| `backtest-baseline.json` | **回测口径基线**：5 条链路的输出指纹（3 份 `check_entry` 判定序列 + 3 个回测引擎指标 + **统一入口 6 个 mode**） | `python scripts/backtest_baseline.py --capture --out docs/refactor/backtest-baseline.json` |
| `scoring-baseline.json` | **评分对拍基线**（第 5 把尺子）：9 个评分入口 × 285 样本，「直接调旧实现」vs「经 ScoringKernel 调适配器」逐字段指纹 | `python scripts/scoring_baseline.py --capture --out docs/refactor/scoring-baseline.json` / `--check` |
| `scripts/scoring_switch_diff.py` | **第 6 把尺子**：`realtime` / `no_realtime` 两条评分分支 × `FEATURE_UNIFIED_SCORING` off/on，整份 `get_stock_score` 响应逐字段 diff，并按分支计数证明 on 侧真走了内核 | `python scripts/scoring_switch_diff.py --check` |
| `../plans/2026-11-07-refactor-plan.md` | 分阶段重构计划本体（阶段划分、兼容策略、验证方式、退出判据） | 人工维护 |

---

## 二、契约快照工具

`scripts/contract_snapshot.py`（零新增依赖，只用 FastAPI/ast/inspect 标准库）

```powershell
# 生成/更新快照
python scripts/contract_snapshot.py --out docs/refactor/contracts

# 与基线比对：任何漂移退出码 = 1（可作 CI 门禁）
python scripts/contract_snapshot.py --check

# 只打印统计，不落盘
python scripts/contract_snapshot.py --summary

# 与指定基线比对（用于验证漂移判据本身是否有效）
python scripts/contract_snapshot.py --check --baseline path/to/other.json
```

### 判定口径（全工具统一，**不容有第二个口径**）

| 类别 | 数量 | 是否入快照 |
|---|---:|---|
| `/api/*` HTTP 端点 | **68** | ✅ 业务契约主体 |
| 非 `/api` HTTP 端点（`/`、`/favicon.svg`、`/sw.js`） | 3 | ✅ |
| WebSocket（`/ws`） | 1 | ✅ |
| 静态挂载 Mount（`/css`、`/js`） | 2 | ❌ 非 API 契约 |
| FastAPI 内置（`/openapi.json`、`/docs`、`/docs/oauth2-redirect`、`/redoc`） | 4 | ❌ |
| **合计 app.routes** | **78** | 快照内 72 条 |

> ⚠️ **口径陷阱（实测踩过）**：用 `grep -c '@app\.(get\|post\|put\|delete)' api.py` 也会得到 **68**，
> 与 `/api/*` 路由数**数字巧合相等但集合不同** —— 前者含 `/`、`/favicon.svg`、`/sw.js`，
> 且**漏掉 `PATCH /api/alerts/rules/{rule_id}`**（PATCH 不在正则里）。
> **以本工具的运行时口径为准**，勿用正则数端点。

### 为什么必须单独固化 async/sync

FastAPI 把 `def` 端点交给**线程池**执行，把 `async def` 端点放在**事件循环**里执行。
重构时把 `async def` 误改成 `def`（或反之）会**静默改变调度语义**：
外部完全无感，但阻塞行为与并发上限已经变了。OpenAPI 规范**不含**此字段，
只能由本工具从 `route.endpoint` 用 `inspect.iscoroutinefunction()` 单独提取
—— 这正是 FastAPI 自己判定线程池归属的**权威口径**。

当前基线读数：`/api/*` 68 条**全部为 async**（sync = 0）⇒ 现存阻塞 IO 全在事件循环里跑。

### 鉴权判定说明

本项目用 `APIKeyHeader` + `Depends` 链，**不使用** FastAPI `SecurityRequirement`
⇒ `dependant.security_requirements` 恒为 `None`，OpenAPI 里也不存在 apiKey security scheme。
因此鉴权只能靠**依赖名**识别（`AUTH_DEP_NAMES = {"verify_api_key"}`）。
基线字段 `auth_dependencies` 记录命中的鉴权依赖，`dependencies` 记录全部依赖。

---

## 三、当前基线摘要（2026-11-07 实测，commit `a7c2265`）

- `/api/*` 端点 **68** 条：GET 49 / POST 15 / DELETE 3 / PATCH 1
- async **68** / sync **0**
- 需鉴权 **65** 条；**免鉴权 3** 条：`POST /api/errors`、`GET /api/health`、`GET /api/scan/status`
  - `/api/health` 健康探针免鉴权属预期；
  - `POST /api/errors`（前端报错上报）与 `GET /api/scan/status`（扫描任务状态）
    未鉴权属**待评估面**，Phase 5 拆分时必须保持不变或走显式决策（不可在重构中顺手改）。
- AST ↔ 运行时端点集合互证：**72 ↔ 72，零差异**

---

## 四、尺子自证有效（Phase 0 退出判据之一）

判据若从未被证明会判红，它可能只是个**永不触发的空判据**。已执行三路验证：

| 验证 | 操作 | 期望 | 实测 |
|---|---|---|---|
| A 合成漂移 | 污染基线副本：async 翻转 + 删端点 + 加端点 | 判红 | ✅ 检出 3 处（ADDED / REMOVED / CHANGED） |
| B **真实扰动** | `api.py` 临时把 `/api/health` 由 `async def` 改为 `def` | 判红 | ✅ 检出 `async: True -> False`；文件字节级还原 |
| C 还原回归 | 还原后重跑 `--check` | PASS | ✅ exit 0 |

复现 B 的方法（注意 `finally` 还原，勿在未还原状态下提交）：
```python
import pathlib, subprocess, sys
api = pathlib.Path("api.py"); orig = api.read_text(encoding="utf-8")
try:
    api.write_text(orig.replace('@app.get("/api/health")\nasync def health',
                                '@app.get("/api/health")\ndef health'), encoding="utf-8")
    print(subprocess.run([sys.executable, "scripts/contract_snapshot.py", "--check"]).returncode)
finally:
    api.write_text(orig, encoding="utf-8")
```

---

## 五、CI 门禁接入（2026-11-07 完成）

```yaml
- name: Contract snapshot check (API 契约漂移门禁)
  # 契约与 Python 小版本无关 —— 只在 3.12 上跑，覆盖 3 个 OS 而非 9 个 job
  # 放在 pytest 之前：门禁须能快速失败，不受全量测试耗时影响
  if: matrix.python-version == '3.12'
  env:
    SKIP_HS300_PREFETCH: '1'
  run: python scripts/contract_snapshot.py --check
```

**三平台验证结果**（run `42290016850`）：

| Job | 契约步骤 | 说明 |
|---|---|---|
| `test (ubuntu-latest, 3.12)` | ✅ success | Linux |
| `test (macos-latest, 3.12)` | ✅ success | macOS |
| `test (windows-latest, 3.12)` | ✅ success | Windows（修复前为 failure） |
| `test (*, 3.10)` / `test (*, 3.11)` | skipped | 条件生效 |

矩阵仍是 **9 个 job（未新增）**；契约检查只在 3.12 × 3 OS 执行。

> ⚠️ **该门禁的结论必须取「步骤级」**：仓库既有 pytest 在三平台均有 3 个失败
> （`tests/test_signal_tracker.py::TestBorrow2AtomicWrite`，portalocker 跨平台），
> 会让 workflow **整体** conclusion 变成 failure，但与契约门禁无关。
> 把契约步骤放在 `Run tests` **之前**，正是为了让门禁能独立跑完并独立出结论。

### 跨平台编码（踩过的坑）

Windows CI 的 `stdout` 默认 **cp1252/cp936**（非 UTF-8），脚本里任何中文 `print` 都会
直接抛 `UnicodeEncodeError` 让门禁崩溃；**本地终端是 UTF-8 ⇒ 本地永远测不出来**。
现采用双保险：① 全部门禁输出为**纯 ASCII**（中文说明在本文件与代码注释里）
② 入口处 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`。

本地复现（不必等 CI）：

```powershell
$env:PYTHONIOENCODING='cp1252'
python scripts/contract_snapshot.py --check   # 修复前崩溃；修复后 exit 0
```

---

## 六、已知限制

1. **导入副作用**：`python scripts/contract_snapshot.py` 会 `import api`，触发
   `后台预获取沪深300数据`（写入 `cache/`，已 gitignore，**不污染工作树** —— 实测导入前后
   `git status --porcelain` 新增变更 = 0）。单次运行约 2.5s。
   **CI 接入前需先确认**：CI 环境无 `D:/TDX/vipdoc` 本地数据时该预取是否安全降级。
2. **鉴权方案名不可得**：如上，OpenAPI 无 security scheme，`auth_dependencies` 是当前能拿到的最强读数。
3. **覆盖率基线尚未生成**：Phase 0 剩余项（CI `--cov` + 按模块分解基线 + 双跑对拍框架）未完成，
   `baseline.md` 中相关章节待补。
