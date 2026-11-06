# docs/refactor/ —— 重构度量基线（RF-01 / Phase 0）

> 本目录是 RF-01 分阶段重构计划的 **Phase 0「度量基线与安全网」** 产物。
> 目的：在动任何业务代码之前，先把「改动是否改变对外行为」变成**机器可判据**。
> 原则：**没有尺子，「可破坏契约」等于盲改。**

---

## 一、目录内容

| 文件 | 作用 | 生成方式 |
|---|---|---|
| `baseline.md` | 规模/覆盖率/端点等**基线读数**台账（各阶段增量对比的参照） | 人工维护 + 脚本回填 |
| `contracts/route-contract-baseline.json` | **路由契约快照**：路径 + 方法 + async/sync 属性 + 鉴权依赖 + 参数 + 响应状态码 | `python scripts/contract_snapshot.py --out docs/refactor/contracts` |
| `contracts/openapi-baseline.json` | FastAPI 标准 OpenAPI 文档（给前端/网关/契约测试比对） | 同上（同一次运行一并产出） |
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

## 五、已知限制

1. **导入副作用**：`python scripts/contract_snapshot.py` 会 `import api`，触发
   `后台预获取沪深300数据`（写入 `cache/`，已 gitignore，**不污染工作树** —— 实测导入前后
   `git status --porcelain` 新增变更 = 0）。单次运行约 2.5s。
   **CI 接入前需先确认**：CI 环境无 `D:/TDX/vipdoc` 本地数据时该预取是否安全降级。
2. **鉴权方案名不可得**：如上，OpenAPI 无 security scheme，`auth_dependencies` 是当前能拿到的最强读数。
3. **覆盖率基线尚未生成**：Phase 0 剩余项（CI `--cov` + 按模块分解基线 + 双跑对拍框架）未完成，
   `baseline.md` 中相关章节待补。
