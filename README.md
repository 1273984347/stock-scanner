# A股盘后选股工具

> **当前版本 v1.3（2026-08-26）**：W001 六文档治理体系落地 + W002 A 股周期顶部预警（进行中）· 详见 [CHANGELOG.md](CHANGELOG.md) 与 [交接文档.md](交接文档.md)。
> **维护契约**：版本行由人工维护，须与 `git rev-parse --short HEAD` 核对；项目变更一律进 CHANGELOG；本文件禁止追加里程碑/批次速记段。

整合 quant-system + ai-hedge-fund + cryptoquant-ai 的 A 股适用版选股系统。

## 核心特性

- **市场体制检测**：TREND_UP/TREND_DOWN/RANGE/RISK_OFF（来自 cryptoquant-ai）
- **宏观风险门控**：根据市场情绪自动调整仓位（来自 cryptoquant-ai）
- **6 大策略信号**：趋势/均值回归/动量/波动率/统计套利/趋势突破
- **波动率调整仓位**：低波动股25%仓位，高波动股10%仓位
- **飞书推送**：自动发送选股信号 + 市场概况 + 风控规则
- **北交所支持**：4/8开头股票本地数据读取

## 市场体制检测（来自 cryptoquant-ai）

| 体制 | 条件 | 行为 |
|:---|:---|:---|
| TREND_UP | 趋势+动量+量能 综合>0.35 | 允许趋势突破买入 |
| TREND_DOWN | 综合<-0.35 | 允许趋势突破卖出 |
| RANGE | -0.35~0.35 | 只允许均值回归 |
| RISK_OFF | 波动率过高 | 禁止所有开仓 |

## 宏观风险门控（来自 cryptoquant-ai）

| 状态 | 市场情绪 | 仓位乘数 | 行为 |
|:---|:---|:---|:---|
| ALLOW_FULL | >-0.10 | 100% | 正常交易 |
| ALLOW_REDUCED | -0.30~-0.10 | 50% | 仓位减半 |
| BLOCK_NEW_RISK | <-0.30 | 0% | 禁止开仓 |

## 使用

### CLI 模式

```bash
python run.py                           # 默认
python run.py --preset aggressive       # 激进
python run.py --preset conservative     # 保守
python run.py --no-push                 # 不推送
python run.py --evaluate                # 因子评价
```

### API 模式

```bash
# 设置 API 密钥（必需）
export API_SECRET_KEY="your-secret-key"

# 启动服务
uvicorn api:app --host 0.0.0.0 --port 8000

# 调用接口
curl -X POST http://localhost:8000/scan \
  -H "X-API-Key: $API_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"strategy": "multi_factor", "limit": 10}'
```

### 环境变量

| 变量 | 说明 | 必需 |
|:---|:---|:---|
| `API_SECRET_KEY` | API 认证密钥 | API 模式必需 |
| `FEISHU_APP_ID` | 飞书应用 AppID | 推送必需 |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | 推送必需 |
| `TUSHARE_TOKEN` | Tushare Token | Tushare 数据源必需 |

## 文件结构

```
stock-scanner/
├── config.py              # 全局配置 (策略/风控/LLM/ML/组合)
├── factors.py             # 因子库 (技术指标/市场体制/宏观门控)
├── scanner.py             # 选股策略主模块 (legacy)
├── backtest.py            # 回测引擎
├── api.py                 # FastAPI 端点 (需要 API_SECRET_KEY)
├── run.py                 # CLI 入口
├── notifier.py            # 飞书推送
├── lib/                   # 核心业务模块
│   ├── data_layer.py      # 多源数据获取/缓存/限流
│   ├── data_source_registry.py  # 数据源注册与标准化路由
│   ├── data_tdx.py        # TDX 本地文件 (含北交所: 4/8开头)
│   ├── data_baostock.py   # Baostock 数据源
│   ├── data_akshare.py    # Akshare 数据源
│   ├── data_tushare.py    # Tushare 数据源
│   ├── strategies.py       # 5大选股策略 + 四维评分
│   ├── pipeline.py        # 策略编排/组合优化/ML集成
│   ├── broker_data/       # broker 拆分模块
│   │   ├── cache.py       # LRUCache
│   │   ├── datatypes.py   # Order/Trade/Holding
│   │   ├── market_impact.py  # 冲击成本模型
│   │   └── trading_constraints.py  # 交易约束
│   ├── broker_core.py     # A股券商核心 (整手取整/T+1)
│   ├── live_broker.py     # 实盘券商 (Paper/TDX)
│   └── ...
├── tests/                 # 测试套件 (208 tests)
└── cache/                 # 数据缓存
```

## 数据源

| 数据源 | 支持市场 | 说明 |
|:---|:---|:---|
| TDX 本地文件 | A股 + 北交所 | 本地 .day 文件，完整支持 |
| Baostock | A股 | 网络数据，免费 |
| Akshare | A股 | 网络数据，免费 |
| Tushare | A股 | 需要 Token |

> 注意：北交所（4/8开头）仅支持 TDX 本地文件数据源

## 测试

```bash
pytest tests/ -v                    # 运行所有测试
pytest tests/ -v --tb=short         # 简短错误信息
pytest tests/test_pipeline.py -v    # 只跑 pipeline 测试
```

测试默认 mock 网络请求，无外部依赖。

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

风控规则：
  止损: -5%
  止盈: +10%
```
