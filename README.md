# A股盘后选股工具

整合 quant-system + ai-hedge-fund + cryptoquant-ai 的 A 股适用版选股系统。

## 核心特性

- **市场体制检测**：TREND_UP/TREND_DOWN/RANGE/RISK_OFF（来自 cryptoquant-ai）
- **宏观风险门控**：根据市场情绪自动调整仓位（来自 cryptoquant-ai）
- **6 大策略信号**：趋势/均值回归/动量/波动率/统计套利/趋势突破
- **波动率调整仓位**：低波动股25%仓位，高波动股10%仓位
- **飞书推送**：自动发送选股信号 + 市场概况 + 风控规则

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

```bash
python run.py                           # 默认
python run.py --preset aggressive       # 激进
python run.py --preset conservative     # 保守
python run.py --no-push                 # 不推送
python run.py --evaluate                # 因子评价
```

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

## 文件结构

```
stock-scanner/
├── config.py      # 配置（预设策略、风控）
├── factors.py     # 因子库（市场体制+宏观门控+6大信号）
├── scanner.py     # 选股策略（5个）
├── notifier.py    # 飞书推送
├── run.py         # 入口脚本
├── backtest.py    # 回测模块
├── TODO.md        # 待办清单
└── cache/         # 数据缓存
```
