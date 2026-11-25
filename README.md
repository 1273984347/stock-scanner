# A股盘后选股工具（stock-scanner）

整合 quant-system + ai-hedge-fund + cryptoquant-ai 的 A 股适用版选股系统。
个人量化投资辅助工具（非机构级，注重实用性）。

> **这是给人看的入口**：项目是什么、怎么跑、怎么用。
> AI 行为约束（硬约束 / 禁区 / 门禁 / 提交纪律）见 [AGENTS.md](AGENTS.md)；
> 文档分工与例外登记见 [RULES.md](RULES.md)。

---

## 核心特性

- **五大选股策略**：涨停板复盘 / 量价突破 / 热门板块 / 龙虎榜 / 多因子
- **四维评分**：L1 基本面 · L2 资金流 · L3 新闻情绪 · L4 技术面（9 评委 / ABSTAIN 机制）
- **市场体制检测**：TREND_UP / TREND_DOWN / RANGE / RISK_OFF
- **宏观风险门控**：按市场情绪自动调整仓位（ALLOW_FULL / ALLOW_REDUCED / BLOCK_NEW_RISK）
- **波动率调整仓位**：低波动股 25%，高波动股 10%
- **飞书推送**：自动发送选股信号 + 市场概况 + 风控规则（带心跳哨兵）
- **北交所支持**：4/8/92 开头股票（新浪源覆盖）
- **数据项目内落地**：K 线 / 指数 / 快照 / 财务 / 资金流先读 `data/store/`，在线源只作刷新

---

## 安装与环境变量

```bash
pip install -r requirements.txt
```

| 变量 | 说明 | 必需 |
|:---|:---|:---|
| `API_SECRET_KEY` | API 认证密钥（随机值，勿照抄示例） | API 模式必需 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书应用凭据 | 推送必需 |
| `TUSHARE_TOKEN` | Tushare Token | Tushare 数据源必需 |
| `TDX_DATA_PATH` | 本地通达信 vipdoc 目录（历史日线/板块） | 否 |
| `ALLOW_MULTI_WORKER` | 显式置 1 才允许多 worker（默认单 worker） | 否 |

> `.env` 含敏感信息，**禁止 commit**。

---

## 快速开始

### CLI

```bash
python run.py                            # 默认（balanced）
python run.py --preset aggressive        # 激进
python run.py --preset conservative      # 保守
python run.py --no-push                  # 不推送飞书
python run.py --evaluate                 # 因子评价
python run.py --backtest --symbols 600519 000858 --days 250
```

> `--backtest` 的 `--symbols` **默认只有 3 只**（000001 / 600519 / 000858），
> 做统计结论前务必显式传更多标的或改用全市场模式。

### API

```bash
export API_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"

# 默认只监听本机回环地址。若需对外提供服务，请在前面加 Nginx/Caddy 做 TLS 与访问控制，
# 不要直接把 --host 改成 0.0.0.0（那会把端点连同配置写入/备份恢复暴露到全网卡）。
uvicorn api:app --host 127.0.0.1 --port 8000

curl -X POST http://localhost:8000/api/scan \
  -H "X-API-Key: $API_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"strategy": "multi_factor", "limit": 10}'
```

> 生产**勿加 `--reload`**（watchfiles 热重载清空进程内任务态/缓存/WS 连接，与
> 单 worker 前提冲突）；确需多 worker 显式 `ALLOW_MULTI_WORKER=1`，跨进程单飞由
> `lib/infra/process_leader.py` 提供（文件锁 + 原子快照，零 Redis）。

常用端点（完整 69 个见 `docs/refactor/contracts/route-ownership.json`）：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/scan` | GET | 选股 + 风控 + 组合优化 |
| `/api/score/{code}` | GET | 单股四维评分 |
| `/api/stock/{code}` | GET | 个股因子详情 |
| `/api/market` | GET | 市场概况 + 风控 |
| `/api/positions` | GET | 持仓概况 |
| `/api/risk` | GET | 完整风控评估 |
| `/api/exposures` | GET | 因子暴露追踪 |
| `/api/dashboard` | GET | 仪表盘汇总 |
| `/api/backtest` | POST | 回测 |

---

## 目录结构

```
stock-scanner/
├── api.py                # FastAPI 装配层（仅 include_router；端点见 routers/）
├── routers/              # 域路由包（common.py + 17 域模块）
├── run.py / run_v2.py    # CLI 入口
├── config/               # 全局配置包（_env/_types/_presets/_runtime/_constants）
├── factors.py            # 因子库（技术指标/市场体制/宏观门控）
├── scanner.py            # 选股策略入口
├── paper_trading.py      # 纸带交易
├── trading_journal.py    # 交易日志
├── backtest/             # 回测引擎（unified.py = 唯一对外入口，6 mode）
├── lib/                  # 核心业务模块，按职责分 8 个子包
│   ├── data/             # 数据层：多源获取/缓存/限流/协议规范化
│   ├── scoring/          # 评分体系：多维打分/因子/评委团/估值
│   ├── strategy/         # 策略层：选股策略/编排管道/调仓
│   ├── risk/             # 风控执行：风控/仓位/约束/成本/滑点
│   ├── portfolio/        # 组合与因子分析：组合构建/优化/IC
│   ├── ml/               # 机器学习：训练/推理/数据管道/监控
│   ├── monitoring/       # 监控告警：异常检测/告警/日志/体制
│   └── infra/            # 横切层：认证/通用工具/LLM 工具/共享状态
├── reporter/             # 报告生成与推送（scan_notify.py / heartbeat.py）
├── scripts/              # 工具脚本（扫描/回测图表/IC 回填/TDX 更新/门禁尺子）
├── tests/                # 测试套件
├── static/               # 前端 Dashboard（HTML/JS/CSS）
├── docs/                 # 文档（refactor/ = 度量基线与踩坑集；adr/ = 决策记录）
├── data/store/           # 项目内数据仓（ADR-0033）
├── cache/                # 运行时缓存与状态（gitignored）
├── AGENTS.md             # AI 行为唯一真源（硬约束 / 门禁 / 验收）
├── RULES.md              # 例外与偏离登记
└── .env                  # 敏感配置（禁止 commit）
```

---

## 核心功能

### 五大选股策略

| 策略 | 函数 | 说明 |
|------|------|------|
| 涨停板复盘 | `get_zt_candidates()` | 追踪涨停后连板股 |
| 量价突破 | `get_volume_breakout_candidates()` | 价格 + 成交量突破 |
| 热门板块 | `get_hot_board_candidates()` | 板块资金流向 |
| 龙虎榜 | `get_lhb_candidates()` | 机构席位跟踪 |
| 多因子 | `get_multi_factor_candidates()` | Alpha 多因子模型 |

### 四维评分体系

| 层级 | 维度 | 评委数 |
|------|------|--------|
| L1 | 基本面 | 9 评委 ABSTAIN |
| L2 | 资金流 | 9 评委 ABSTAIN |
| L3 | 新闻/情绪 | 9 评委 ABSTAIN |
| L4 | 技术面 | 9 评委 ABSTAIN |

权重：基本面 35% · 资金流 35% · 新闻情绪 20% · 技术面 10%。
**缺失维会被剔除并归一其余三维权重**（不用 0 分冒充「中性」，见 `AGENTS.md` §6.2）。

### 市场体制检测

| 体制 | 条件 | 行为 |
|------|------|------|
| TREND_UP | 综合 > 0.35 | 允许趋势突破买入 |
| TREND_DOWN | 综合 < -0.35 | 允许趋势突破卖出 |
| RANGE | -0.35 ~ 0.35 | 只允许均值回归 |
| RISK_OFF | 波动率过高 | 禁止所有开仓 |

### 宏观风险门控

| 状态 | 情绪阈值 | 仓位乘数 | 行为 |
|------|----------|----------|------|
| ALLOW_FULL | > -0.10 | 100% | 正常交易 |
| ALLOW_REDUCED | -0.30 ~ -0.10 | 50% | 仓位减半 |
| BLOCK_NEW_RISK | < -0.30 | 0% | 禁止开仓 |

---

## 数据源

K 线走多源自动切换（ADR-0027），真源 = `lib/data/data_source_registry.py`：

| 场景 | 源顺序 | 说明 |
|:--|:--|:--|
| 实时选股（默认栈） | `sina_kline → tencent_kline → baostock → tdx → adata → westock → tushare → akshare → tdx_local_*` | 新浪覆盖最广（含北交所/指数），**不复权** |
| 回测（`prefer_adjusted`） | `tencent_kline → baostock → akshare → …` | 只有腾讯给前复权；**新浪不复权源不得进回测栈**（除权日会造假跳空） |

2026-11-19 实测背景：8 个既有源当日**全部返回 0 行**（baostock `10001011` 黑名单、
akshare 走 `push2his` 不可达、tushare 无 `daily` 权限、adata/westock 缺 Node、
本地 TDX 已删），而新浪/腾讯网页网关都返到最近交易日 ⇒ **「源栈全 0」不等于「没有数据」**。
数据健康用 `python scripts/kline_coverage_probe.py --strict` 一键核验（6 个代表标的）。

- 全市场**实时行情**走三级源栈（ADR-0024）：新浪全市场（`ak.stock_zh_a_spot`，实测
  5579 行 / 12.3s，含北交所）→ 腾讯批量（`qt.gtimg.cn`，200 码/请求，按上次成功代码表
  重建）→ pytdx 公共服务器样本股（仅 ~28 只，最后兜底）。低于 4000 行的降级快照
  **不写缓存**并逐条告警 —— 避免用样本帧算全市场涨跌比。
- 东财是**按 host 部分可用**（ADR-0025）：`push2`/`push2his` 可能不通，而
  `push2ex`（涨停池/龙虎榜）与 `emweb`（F10 财务）正常。排查用
  `python scripts/host_health_probe.py`；不要用「东财能不能通」下结论。
- 北交所（4/8/92 开头）由**新浪源**覆盖（本地 TDX 已删，见 ADR-0017）。
- 离线环境可跑历史选股与回测；实时扫描需要能访问上述行情网关的网络。

### 资金流

同花顺即时资金流（批量层，1 call/小时）实测资金流列几乎全为 NaN ⇒ 批量层实为失效，
**逐股源为主要路径**。缺失显式化语义见 `AGENTS.md` §6.2 与坑表 P-114。

---

## ML 模型

- 训练：XGBoost 20 日预测；模型存储在 `cache/models/`（按版本目录 + `manifest.json`）
- 推理入口在 `lib/ml/`，在线监控在 `lib/monitoring/`

> ⚠️ **引用历史数字前先核对样本基础**：早期文档记录的「Rank IC 0.074 / 融合后夏普 3.80」
> 来自 2026-05 的归档样本。2026-11-20 实测发现当前仓内 walk-forward 读数
> **95/95 份全部 `insufficient_rounds`、`stable_rounds=0`**，且默认回测标的只有 3 只
> ⇒ 这两个数字不构成当前可复现的策略有效性证据。

---

## 测试与开发

```bash
pytest tests/ -v                       # 全部测试（默认 mock 网络，无外部依赖）
pytest tests/test_pipeline.py -v       # 只跑一个文件
python -m py_compile <file>.py          # 单文件语法检查
```

**提交前必跑 L0–L10 分层门禁（不可跳级）**，命令与通过标准见 `AGENTS.md` §4。

---

## 常用脚本

```bash
python scripts/daily_scan.py                    # 每日扫描（--no-push 不推送）
python scripts/backtest_charts.py --symbols 600519,000858 --days 500
python scripts/backfill_ic.py                   # IC 历史回填
python -m lib.scoring.factor_optimizer --eval-only --symbols 600519 000858
python scripts/host_health_probe.py             # 数据源 host 级诊断
python scripts/kline_coverage_probe.py --strict # 日线覆盖诊断
```

统一回测入口（**唯一对外入口**，见 `AGENTS.md` §2.1）：

```python
from backtest.unified import BacktestRunner, BacktestRequest

runner = BacktestRunner()
result = runner.run(mode="rebalance", symbols=["600519", "000858"], days=300)
# mode: simple(单股) / full(全市场) / rebalance(调仓) / factor(因子)
```

---

## TDX 数据更新（可选路径）

**数据路径**：`D:/TDX/vipdoc`（通过 `TDX_DATA_PATH` 环境变量配置）
**下载页面**：<https://www.tdx.com.cn/article/alldata.html>

```bash
python scripts/update_tdx_data.py                # 增量更新（跳过无变化的包）
python scripts/update_tdx_data.py --force        # 强制重新下载全部 16 个包
python scripts/update_tdx_data.py --package shlday  # 只下载单个包
python scripts/update_tdx_data.py --list         # 列出所有包
```

增量机制：HTTP HEAD 检查 `Last-Modified`，元数据存 `D:/TDX/update_meta.json`。

**定时任务**：用 `powershell -ExecutionPolicy Bypass -File scripts\bootstrap_schedules.ps1`
注册（晨报 08:45 / paper 日报 16:00 / TDX 更新 18:30，幂等可重跑；**入库不执行，需用户手动跑**）。
任务失联由推送心跳兜底（`reporter/heartbeat.py`，晨报自检）。

### 数据包清单（共 16 个）

| 包名 | 内容 | 路径 | 文件数 |
|:-----|:-----|:-----|-------:|
| `shlday` | 上证所有证券日线(TCKV4=0) | `sh/lday/` | 20,686 |
| `szlday` | 深证所有证券日线(TCKV4=0) | `sz/lday/` | 19,383 |
| `bjlday` | 北证所有证券日线 | `bj/lday/` | 582 |
| `shzsday` | 上证指数日线 | `sh/zs/` | 339 |
| `szzsday` | 深证指数日线 | `sz/zs/` | 463 |
| `tdxzs_day` | 通达信板块指数日线 | `tdx/zs/` | 1,317 |
| `shlday_4` | 上证所有证券日线(TCKV4=1 前复权) | `sh/lday_4/` | 20,686 |
| `szlday_4` | 深证所有证券日线(TCKV4=1 前复权) | `sz/lday_4/` | 19,736 |
| `shqqday` | 上海股票期权日线 | `sh/option/` | 11,680 |
| `szqqday` | 深圳股票期权日线 | `sz/option/` | 7,544 |
| `xsbday` | 股转系统日线 | `xsb/lday/` | 11,163 |
| `62day` | 中证指数日线 | `csi/zs/` | 3,010 |
| `ggtday` | 港股通日线 | `ggt/lday/` | 3,467 |
| `33day` | 开放式基金日线 | `fund/open/` | 29,773 |
| `34day` | 货币型基金日线 | `fund/money/` | 1,207 |
| `38day` | 宏观指标日线 | `macro/lday/` | 219 |

其它可用包（需时单独下载）：`tdxfin`（财务报表）、`tdxgp`（高频/Tick）、
`hsjday`（沪股通日线）、`ScJyData_zbca`（特定市场数据）。

---

## 飞书推送内容

```
盘后选股信号 (2026-05-13 03:16)
策略: balanced

【市场概况】
  情绪分数: -0.49
  宏观门控: BLOCK_NEW_RISK
  仓位乘数: 0%
  说明: 市场风险高，禁止新开仓位

【涨停板复盘】10 只
...

【龙虎榜】10 只
...

风控规则:
  止损: -5%
  止盈: +10%
```

---

## 文档导航

| 文档 | 回答什么问题 |
|:---|:---|
| [AGENTS.md](AGENTS.md) | AI 必须怎么行动（硬约束 / 门禁 / 提交纪律） |
| [RULES.md](RULES.md) | 哪里和全局不一样（例外登记） |
| [docs/refactor/pitfalls.md](docs/refactor/pitfalls.md) | 为什么会踩坑（现象→根因→出坑→预防） |
| [docs/refactor/README.md](docs/refactor/README.md) | 度量尺子口径与自证记录 |
