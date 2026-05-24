# CLAUDE.md — Stock Scanner (个人量化选股系统)

## 工作流

**开发流程**: `需求 → 验证 → 实现 → 回测 → 记录`

**调试流程**: `查 Skill → 诊断 → 修复 → 验证 → 沉淀`

**复杂调试**: `/systematic-debugging` 系统化排查

---

## 项目身份

- **类型**: Python 量化选股工具 + REST API 服务
- **定位**: A股个人量化投资辅助系统 (非机构级，注重实用性)
- **技术栈**: Python 3.12 / FastAPI / pandas / XGBoost / LightGBM
- **数据源**: Tushare (主) → AkShare → WeStock → 通达信 → Baostock → 东方财富

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 核心语言 | Python 3.12 |
| 数据处理 | pandas, numpy, scipy |
| 量化框架 | backtrader, adata |
| ML 模型 | XGBoost, LightGBM |
| Web 框架 | FastAPI + uvicorn |
| 缓存 | Parquet 文件 + SQLite (audit.db) |
| 外部 API | 飞书 Webhook、DeepSeek/Kimi/Mimo LLM |
| 部署 | uvicorn (API 服务) |

---

## 项目结构

```
stock-scanner/
├── config.py              # 全局配置 (策略/风控/LLM/ML/组合)
├── factors.py             # 因子库 (技术指标/市场体制/宏观门控)
├── scanner.py             # 选股策略主模块 (legacy, 建议迁移至 lib/strategies.py)
├── backtest.py            # 回测引擎
├── api.py                 # FastAPI 端点
├── run.py                 # CLI 入口
├── notifier.py            # 飞书推送
├── lib/                   # 核心业务模块
│   ├── data_layer.py      # 多源数据获取/缓存/限流
│   ├── data_source_registry.py  # 数据源注册与标准化路由
│   ├── data_tdx.py        # TDX 本地文件数据源 (含北交所支持)
│   ├── data_baostock.py   # Baostock 数据源
│   ├── data_akshare.py    # Akshare 数据源
│   ├── data_tushare.py    # Tushare 数据源
│   ├── strategies.py      # 5大选股策略 + 四维评分
│   ├── pipeline.py        # 策略编排/组合优化/ML集成
│   ├── broker_data/       # broker 拆分模块
│   │   ├── __init__.py
│   │   ├── cache.py       # LRUCache
│   │   ├── datatypes.py   # Order/Trade/Holding
│   │   ├── market_impact.py  # 冲击成本模型
│   │   └── trading_constraints.py  # 交易约束
│   ├── broker_core.py     # A股券商核心 (整手取整/T+1)
│   ├── live_broker.py     # 实盘券商 (Paper/TDX)
│   ├── risk_manager.py    # 风控 (止损/回撤)
│   ├── position_size.py   # 仓位管理
│   ├── analyzer.py        # 性能分析
│   ├── scorecard.py       # 评分卡 (四层评委团)
│   ├── judge_panel.py     # 评委团机制
│   ├── evaluator.py       # 因子评价 (IC/单调性)
│   ├── ml_predictor.py    # ML 预测
│   ├── ml_trainer.py      # ML 训练
│   ├── portfolio.py       # 组合管理
│   ├── portfolio_optimizer.py  # 组合优化
│   ├── walkforward.py     # Walk-forward 验证
│   ├── llm_utils/         # LLM 工具
│   └── cache.py           # 通用缓存工具
├── tests/                 # 测试套件 (208 tests, 网络隔离)
├── cache/                 # 数据缓存
│   ├── *.parquet          # 行情/指数缓存
│   ├── audit.db           # 审计日志
│   ├── models/            # ML 模型
│   └── backtest_results/  # 回测结果
└── .env                   # 敏感配置 (飞书AppID/Secret/Tushare Token)
```

---

## 核心功能

### 五大选股策略

| 策略 | 函数 | 说明 |
|------|------|------|
| 涨停板 | `get_zt_candidates()` | 追踪涨停后连板股 |
| 量价突破 | `get_volume_breakout_candidates()` | 价格+成交量突破 |
| 热门板块 | `get_hot_board_candidates()` | 板块资金流向 |
| 龙虎榜 | `get_lhb_candidates()` | 机构席位跟踪 |
| 多因子 | `get_multi_factor_candidates()` | Alpha多因子模型 |

### 四维评分体系

| 层级 | 维度 | 评委数 |
|------|------|--------|
| L1 | 基本面 | 9评委 ABSTAIN |
| L2 | 资金流 | 9评委 ABSTAIN |
| L3 | 新闻/情绪 | 9评委 ABSTAIN |
| L4 | 技术面 | 9评委 ABSTAIN |

### 市场体制检测

| 体制 | 条件 | 行为 |
|------|------|------|
| TREND_UP | 综合 > 0.35 | 允许趋势突破买入 |
| TREND_DOWN | 综合 < -0.35 | 允许趋势突破卖出 |
| RANGE | -0.35 ~ 0.35 | 只允许均值回归 |
| RISK_OFF | 波动率过高 | 禁止所有开仓 |

### 宏观风险门控

| 状态 | 情绪阈值 | 仓位乘数 |
|------|----------|----------|
| ALLOW_FULL | > -0.10 | 100% |
| ALLOW_REDUCED | -0.30 ~ -0.10 | 50% |
| BLOCK_NEW_RISK | < -0.30 | 0% |

### API 端点

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

## 工作流规则

### Python 代码验证

```bash
# 语法检查
python -m py_compile <file.py>

# 运行测试
pytest tests/ -v

# API 启动
uvicorn api:app --reload --port 8000
```

### 数据源优先级

Tushare (主) → AkShare → WeStock → 通达信 → Baostock → 东方财富

自动降级，更高优先级失败时切换。

### 环境变量

`.env` 文件包含敏感信息，**禁止 commit**:
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `TUSHARE_TOKEN`

---

## ML 模型状态

- **模型**: XGBoost 20日预测
- **Rank IC**: 0.074
- **融合后夏普比率**: 3.80
- **线程安全**: 已清零 (133 tests PASS)

---

## Skill 速查

| 需求 | Skill |
|:-----|:------|
| 系统化调试 | `/systematic-debugging` |
| 代码审查 | `/code-review` |
| 完成前验证 | `/verification-before-completion` |

## Task 驱动工作流

### Bug 修复
```
1. TaskCreate: "修复 <问题> - <简述>"
2. 诊断问题根源 (`/systematic-debugging`)
3. 修复 + pytest 验证
4. experience-capture 沉淀
5. git commit
```

### 策略/因子修改
```
1. TaskCreate: "修改 <策略/因子>"
2. 修改 config.py / factors.py
3. pytest tests/ 验证
4. 回测验证 (api.py 或 run.py)
5. git commit
```

## 验证标准 (分层)

| 层级 | 命令 | 用途 |
|:-----|:-----|:------|
| **L0** | `python -m py_compile <file>.py` | 语法检查 |
| **L1** | `pytest tests/ -v` | 单元测试 |
| **L2** | `python run.py --help` | CLI 验证 |
| **L3** | `uvicorn api:app --reload` | API 服务验证 |

## 强制触发规则

| 条件 | 必须执行 |
|:-----|:---------|
| 工作开始前 | `/reflection-retriever` 查经验 (代码/CPU 密集型项目) |
| 数据源修改 | 测试多数据源降级 |
| 策略逻辑修改 | 回测验证 + Walk-forward |
| Bug 调试 >10min | `/experience-capture` |
| P0/P1 Bug | `/fix-verify-loop` |

## 常见错误

| 症状 | 原因 | 解决 |
|:-----|:-----|:------|
| Tushare Token 过期 | 数据获取失败 | 更新 `.env` 中的 Token |
| 数据降级触发 | 高优先级数据源失败 | 检查网络/账户状态 |
| 回测结果异常 | 涨停/停牌处理逻辑 | 检查 `broker.py` |
| API 启动失败 | Port 被占用 | `netstat -ano \| grep :8000` |

---

## Session 结束检查

```
1. [ ] pytest tests/ 通过
2. [ ] 回测结果无异常
3. [ ] git status 确认
```

---

## Task 状态追踪

| 状态 | 含义 |
|:-----|:------|
| `pending` | 等待开始 |
| `in_progress` | 执行中 |
| `completed` | 已完成 |

---

## API 服务管理

### 启动服务
```bash
uvicorn api:app --reload --port 8000
```

### 停止服务
```bash
# 查 port
netstat -ano | grep :8000 | grep LISTEN
# 杀进程
taskkill //F //PID <PID>
```

### 测试 API
```bash
curl http://localhost:8000/api/market
curl http://localhost:8000/api/scan
```

---

## 数据源调试

| 症状 | 排查方式 |
|:-----|:----------|
| 数据获取失败 | 检查 `.env` Token 是否有效 |
| 频繁降级 | 检查高优先级数据源网络 |
| 缓存过期 | 删除 `cache/*.parquet` 重新获取 |

---

## 关键文件

| 文件 | 说明 |
|:-----|:-----|
| `config.py` | 全局配置 (策略/风控/LLM/ML) |
| `scanner.py` | 选股策略入口 |
| `lib/pipeline.py` | 策略编排核心 |
| `lib/data_layer.py` | 多源数据层 |
| `.env` | 敏感配置 (禁止 commit) |
| `cache/models/` | ML 模型存储 |
| `scripts/update_tdx_data.py` | TDX 日K线数据更新脚本 |

---

## TDX 数据更新

**数据路径**: `D:/TDX/vipdoc`（通过 `TDX_DATA_PATH` 环境变量配置）

**下载页面**: `https://www.tdx.com.cn/article/alldata.html`

**更新脚本**:
```bash
python3 scripts/update_tdx_data.py              # 下载全部 16 个包
python3 scripts/update_tdx_data.py --package shlday  # 只下载单个包
python3 scripts/update_tdx_data.py --list       # 列出所有包
```

**定时任务**: 每个工作日 18:30 自动运行 (收盘后 ~3.5 小时)

**环境变量**:
```bash
export TDX_DATA_PATH="D:/TDX/vipdoc"
```

### 数据包清单（共 16 个）

| 包名 | 内容 | 路径 | 文件数 |
|:-----|:-----|:-----|-------:|
| `shlday` | 上证所有证券日线(TCKV4=0) | `sh/lday/` | ~20,620 |
| `szlday` | 深证所有证券日线(TCKV4=0) | `sz/lday/` | ~19,326 |
| `bjlday` | 北证所有证券日线 | `bj/lday/` | 580 |
| `shzsday` | 上证指数日线 | `sh/zs/` | 339 |
| `szzsday` | 深证指数日线 | `sz/zs/` | 463 |
| `tdxzs_day` | 通达信板块指数日线 | `tdx/zs/` | 1,317 |
| `shlday_4` | 上证所有证券日线(TCKV4=1 前复权) | `sh/lday_4/` | ~20,620 |
| `szlday_4` | 深证所有证券日线(TCKV4=1 前复权) | `sz/lday_4/` | ~19,679 |
| `shqqday` | 上海股票期权日线 | `sh/option/` | 11,656 |
| `szqqday` | 深圳股票期权日线 | `sz/option/` | 7,536 |
| `xsbday` | 股转系统日线 | `xsb/lday/` | 11,150 |
| `62day` | 中证指数日线 | `csi/zs/` | 3,010 |
| `ggtday` | 港股通日线 | `ggt/lday/` | 3,464 |
| `33day` | 开放式基金日线 | `fund/open/` | 29,728 |
| `34day` | 货币型基金日线 | `fund/money/` | 1,207 |
| `38day` | 宏观指标日线 | `macro/lday/` | 219 |

### 其他可用数据包（不在日常更新范围，需要时单独下载）

| 包名 | 内容 |
|:-----|:-----|
| `tdxfin` | 财务报表数据（季度/年度） |
| `tdxgp` | 高频数据（分时/Tick） |
| `hsjday` | 沪股通日线 |
| `ScJyData_zbca` | 特定市场数据 |

---

## 维护提示

- `config.py` 是全局配置核心，修改前确认影响范围
- `lib/pipeline.py` 是策略编排核心，修改后运行回测验证
- 数据源适配器在 `lib/data_*.py`，新增数据源时参考现有模式
- ML 模型存储在 `cache/models/`，更新后重新验证 Walk-forward
