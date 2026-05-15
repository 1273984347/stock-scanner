"""P1-4 / P2-1 分析脚本：回测路径对比 + 评委团按组验证"""
import sys, os, json, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
logging.basicConfig(level=logging.WARNING, format="%(message)s")

import numpy as np
import pandas as pd

print("="*70)

# ════════════════════════════════════════════════
# P1-4: 回测路径对比
# ════════════════════════════════════════════════
print("P1-4: 回测路径统一对比")
print("-"*70)

from backtest import run_factor_backtest
from lib.ml_backtest import MLBacktest

# backtest.py 路径: factor timing strategy (事件驱动)
print("[backtest.py] 因子择时策略（事件驱动）")
r1 = run_factor_backtest(["000001", "600519", "000858"], days=250, confidence_threshold=0.3)
if r1 and r1["trade_count"] > 0:
    m = r1["metrics"]
    print(f"  夏普={m.sharpe_ratio:.4f}, 收益={m.total_return:.4%}, 交易={r1['trade_count']}")
else:
    print("  无交易（因子择时信号频率极低）")

# MLBacktest 路径: ML cross-sectional ranking (截面排序)
print()
print("[MLBacktest] ML 截面排序（月频多空）")
panel = pd.read_parquet("cache/panel_2015.parquet")
panel["date"] = pd.to_datetime(panel["date"])
dates = sorted(panel["date"].unique())
test_dates = set(dates[int(len(dates)*0.8):])
p_test = panel[panel["date"].isin(test_dates)].copy()

import xgboost as xgb
with open("cache/models/xgb_20d_v2/v1/manifest.json") as f: mf = json.load(f)
model = xgb.Booster(); model.load_model("cache/models/xgb_20d_v2/v1/xgb_20d_v2.model")
feats = [c for c in mf["feature_cols"] if c in panel.columns]
for c in mf["feature_cols"]:
    if c not in panel.columns: panel[c] = 0.5
ms_all = model.predict(xgb.DMatrix(panel[feats].fillna(0.5)))
ms_test = pd.Series(ms_all, index=panel.index)[panel["date"].isin(test_dates)]

bt = MLBacktest({"target_col":"excess_20d","top_pct":0.1,"transaction_cost":0.01,"step":20})
r2 = bt.run(p_test, ml_scores=ms_test)
m2 = r2["metrics"]["ml_only"]
print(f"  夏普={m2['sharpe']:.4f}, 年化收益={m2['annual_return']:.4%}, 样本={r2['metrics']['ml_only']['n_days']}期")

print(f"\n结论: 两个回测路径不可直接对比——backtest.py是因子择时(事件驱动)，")
print(f"MLBacktest是截面排序(定期重平衡)。建议以后统一使用MLBacktest评估策略。")

# ════════════════════════════════════════════════
# P2-1: 评委团按组回测验证
# ════════════════════════════════════════════════
print()
print("="*70)
print("P2-1: 评委团按组回测验证")
print("="*70)
print()
print("注意：评委团规则依赖基本面数据(ROE/PB/毛利率等)，当前数据路径中这些字段")
print("均为None，规则无法真实触发。验证计划将在基本面数据就绪后执行。")
print()
print("各组评委投票触发条件依赖的数据:")
judge_data = {
    "经典价值": "ROE, PB, 负债率, 毛利率——全部不可用",
    "成长": "动量、量比、RSI、波动率——部分可用(来自factor数据)",
    "宏观对冲": "动量、波动率、RSI、vol_ratio——全部可用",
    "技术趋势": "动量、RSI、vol_ratio——全部可用",
    "中国价投": "ROE, 毛利率, PE——PE可用, ROE/毛利率不可用",
    "A股游资": "动量、RSI、vol_ratio——全部可用",
    "AI 量化": "ML模型预测——全部可用(已由xgb_20d_v2替代)",
}
for group, deps in judge_data.items():
    print(f"  {group}: {deps}")

print()
recommendations = {
    "可保留(数据完整)": ["宏观对冲", "技术趋势", "A股游资", "AI 量化"],
    "暂无效(基本面缺失)": ["经典价值", "中国价投"],
    "建议合并到因子系统": ["成长"],
}
for status, groups in recommendations.items():
    print(f"  [{status}]: {', '.join(groups)}")

print()
print("建议: 将依赖基本面的评委组(经典价值/中国价投)暂时全部ABSTAIN,")
print("成长派逻辑已由因子系统覆盖，可移除。实际仅保留宏观+技术+游资+ML。")
print("当前已有ABSTAIN机制(本次会话实现)，无需额外代码修改。")
