"""
dashboard.py - V2 策略 Dashboard（Streamlit）

运行方式：
    streamlit run dashboard.py

功能：
    - 市场状态（HS300 四态，实时检测）
    - V2 策略今日候选股
    - 当前持仓（来自 trading_journal）
    - V2 回测摘要指标
    - 券商连接状态（TDXBroker）
"""

import streamlit as st
import pandas as pd
import os
import sys
from datetime import datetime

sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from lib.market_regime import (
    detect_market_regime_from_hs300,
    TREND_UP, MEAN_REVERSION, COMPRESSION, TREND_DOWN,
    REGIME_POSITION_SCALAR,
)
from lib.technical_signals import ema
from lib.data_layer import get_stock_hist, get_index_hist
from trading_journal import TradingJournal


# ============================================================
# 工具函数
# ============================================================

def compute_indicators(df):
    df = df.copy()
    df['EMA5'] = ema(df['close'], 5)
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    return df


def check_v2_entry(df, i):
    if i < 20:
        return False
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    ma5_cross = (row['EMA5'] > row['EMA21']) and (prev['EMA5'] <= prev['EMA21'])
    price_ok = row['close'] > row['EMA5']
    return bool(ma5_cross and price_ok)


def scan_v2_candidates_live(stock_codes, limit=50):
    """实时扫描 V2 候选股（限制数量避免太慢）"""
    hs300 = get_index_hist('000300', days=60)
    hs300['EMA21'] = ema(hs300['close'], 21)
    hs300['pvem'] = (hs300['close'] / hs300['EMA21'] - 1) * 100
    hs300['ema_slope_pct'] = hs300['EMA21'].pct_change(5) * 100

    def classify_regime(row):
        t = row['pvem'] / 100
        s = row['ema_slope_pct']
        if pd.isna(t) or pd.isna(s):
            return MEAN_REVERSION
        if t > 0.01 and s > 0.05:
            return TREND_UP
        elif t < -0.01 and s < -0.05:
            return TREND_DOWN
        else:
            return MEAN_REVERSION

    hs300['regime'] = hs300.apply(classify_regime, axis=1)
    latest_hs300 = hs300.iloc[-1]
    hs300_regime = latest_hs300['regime']
    hs300_pvem = latest_hs300['pvem']

    if not (latest_hs300['close'] > latest_hs300['EMA21']):
        return [], hs300_regime, hs300_pvem  # 市场下跌，禁止

    candidates = []
    for code in stock_codes:
        try:
            df = get_stock_hist(code, days=60)
            if df is None or len(df) < 30:
                continue
            df = compute_indicators(df)

            # 检查 MEAN_REVERSION 过滤
            if hs300_regime == MEAN_REVERSION and hs300_pvem <= 3.0:
                continue

            # V2 入场信号（金叉）
            entered, _ = check_v2_entry(df, len(df) - 1)
            if not entered:
                continue

            row = df.iloc[-1]
            stock_ret = float(df['close'].pct_change(5).iloc[-1] * 100)

            candidates.append({
                '代码': code,
                '最新价': round(float(row['close']), 2),
                'EMA5': round(float(row['EMA5']), 2),
                'EMA21': round(float(row['EMA21']), 2),
                '5日涨幅%': round(stock_ret, 2),
                '信号': 'MA5/MA21金叉',
                '相对HS300': round(stock_ret - hs300_pvem, 2),
            })
        except Exception:
            continue

    candidates.sort(key=lambda x: x['相对HS300'], reverse=True)
    return candidates[:limit], hs300_regime, hs300_pvem


# ============================================================
# V2 回测基准指标（硬编码，来自 backtest_engine.py 的基准结果）
# ============================================================

V2_BENCHMARK = {
    "总交易数": 156,
    "胜率": "38.5%",
    "均值": "+2.37%",
    "盈亏比": "2.18",
    "最大回撤": "-6.88%",
    "总收益": "+37.0%",
    "timeout胜率": "90%",
    "trailing胜率": "36%",
    "trend_break胜率": "3%",
    "说明": "27只精选A股，2024-01 至 2026-05，V2趋势持有策略",
}

V2_BENCHMARK_1PCT = {
    "总交易数": 90,
    "胜率": "43.3%",
    "均值": "+3.18%",
    "盈亏比": "2.43",
    "最大回撤": "-4.44%",
    "总收益": "+28.6%",
    "说明": "27只 + 相对强度阈值1%，交易量减半但质量提升",
}


# ============================================================
# Streamlit 页面
# ============================================================

st.set_page_config(
    page_title="V2 策略 Dashboard",
    page_icon="📊",
    layout="wide",
)

st.title("📊 V2 A股策略 Dashboard")
st.caption(f"更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# 侧边栏
with st.sidebar:
    st.header("V2 策略说明")
    st.markdown("""
    **入场规则**：
    - MA5/MA21 金叉 + 价格站上 EMA5
    - HS300 > EMA21（市场过滤）
    - MEAN_REVERSION 市场：偏离 >3%

    **出场规则**：
    - ATR × 2 止损（全程）
    - 10日低点移动止损
    - EMA21 辅助（前10天）
    - 20天到期

    **推荐参数**：
    - 精选股池（蓝筹）：相对强度阈值 1%
    - 宽覆盖：禁用相对强度过滤
    """)
    st.markdown("---")
    st.markdown("**数据来源**: 本地 TDX 文件")
    st.markdown("**Broker**: TDXBroker（需通达信客户端）")


# ---- 主区域：4个 Tab ----
tab_status, tab_candidates, tab_positions, tab_benchmark = st.tabs(
    ["📈 市场状态", "🎯 今日候选", "💼 持仓", "📋 基准指标"]
)

# ============================================================
# Tab 1: 市场状态
# ============================================================
with tab_status:
    regime = detect_market_regime_from_hs300(days=60)
    regime_name = regime['regime']
    confidence = regime['confidence']
    position_min, position_max = regime['position_scalar']
    indicators = regime.get('indicators', {})

    # 颜色
    color_map = {
        TREND_UP: ("🟢", "积极做多"),
        MEAN_REVERSION: ("🔵", "短线回归"),
        COMPRESSION: ("🟠", "少做等突破"),
        TREND_DOWN: ("🔴", "禁止开仓"),
    }
    emoji, label = color_map.get(regime_name, ("⚪", "未知"))

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("市场状态", f"{emoji} {regime_name}", label)
    with col2:
        st.metric("置信度", f"{confidence:.0%}")
    with col3:
        st.metric("仓位范围", f"{position_min:.0%} – {position_max:.0%}")
    with col4:
        if indicators:
            st.metric("EMA斜率", f"{indicators.get('ema_slope', 0):.3f}%")

    st.markdown("---")

    # 详细指标
    if indicators:
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**趋势指标**")
            st.write(f"- 价格 vs EMA21: {indicators.get('price_vs_ema', 0)*100:.2f}%")
            st.write(f"- EMA斜率(5日): {indicators.get('ema_slope', 0):.3f}%")
            st.write(f"- 趋势得分: {indicators.get('trend_score', 0):.3f}")
            st.write(f"- Donchian斜率: {indicators.get('dc_slope', 0):.3f}%")
        with col_b:
            st.markdown("**波动指标**")
            st.write(f"- ATR比率: {indicators.get('atr_ratio', 0):.3f}")
            st.write(f"- 压缩度: {indicators.get('compression', 0):.3f}")
            st.write(f"- 波动扩张: {indicators.get('vol_expanding', False)}")

    # 操作建议
    st.markdown("---")
    if regime_name == TREND_DOWN:
        st.error("⛔ 市场下跌趋势：**禁止开仓**。持有仓位可减仓。")
    elif regime_name == COMPRESSION:
        st.warning("🟠 市场压缩区间：少做等突破，仓位 0%-25%。")
    elif regime_name == MEAN_REVERSION:
        st.info("🔵 均值回归市场：短线回归操作，仓位 25%-50%，等待金叉信号。")
    else:
        st.success("🟢 上升趋势：积极做多，仓位 75%-100%。")


# ============================================================
# Tab 2: 今日候选
# ============================================================
with tab_candidates:
    st.markdown("### 🎯 V2 策略今日候选股（MA5/MA21 金叉）")

    # 扫描参数
    col_scan1, col_scan2 = st.columns([1, 3])
    with col_scan1:
        scan_limit = st.selectbox("扫描数量", [20, 50, 100, 200], index=1)
    with col_scan2:
        st.caption("基于本地 TDX 数据（可能有延迟）")

    # 生成候选股列表（使用前N只）
    from pathlib import Path
    sh_dir = Path('D:/TDX/vipdoc/sh/lday')
    sz_dir = Path('D:/TDX/vipdoc/sz/lday')
    all_stocks = []
    for f in sh_dir.iterdir():
        if f.name.endswith('.day'):
            code = f.name[2:-4]
            if code.startswith(('600', '601', '603')):
                all_stocks.append(code)
    for f in sz_dir.iterdir():
        if f.name.endswith('.day'):
            code = f.name[2:-4]
            if code.startswith(('000', '002')):
                all_stocks.append(code)

    # 取样（避免全量扫描太慢）
    import random
    random.seed(42)
    sample_stocks = random.sample(all_stocks, min(scan_limit, len(all_stocks)))

    with st.spinner(f"正在扫描 {len(sample_stocks)} 只股票..."):
        candidates, hs300_regime, hs300_pvem = scan_v2_candidates_live(sample_stocks, limit=30)

    if candidates:
        st.success(f"找到 {len(candidates)} 只候选股票（显示前 {len(candidates)} 只）")
        df_cand = pd.DataFrame(candidates)
        st.dataframe(df_cand, use_container_width=True, hide_index=True)
    else:
        if hs300_regime in (TREND_DOWN, COMPRESSION):
            st.info(f"当前市场状态为 {hs300_regime}，不适合开仓。")
        elif hs300_regime == MEAN_REVERSION and hs300_pvem <= 3.0:
            st.info(f"当前市场为 MEAN_REVERSION，HS300 趋势不够强（{hs300_pvem:.2f}%），等待更好机会。")
        else:
            st.info("今日无 MA5/MA21 金叉信号。")


# ============================================================
# Tab 3: 持仓
# ============================================================
with tab_positions:
    st.markdown("### 💼 当前持仓（来自 trading_journal）")

    journal = TradingJournal()
    open_trades = journal.get_open_trades()
    all_trades = journal.get_all_trades()

    col_p1, col_p2 = st.columns(2)
    with col_p1:
        st.metric("当前持仓", len(open_trades))
    with col_p2:
        st.metric("历史总交易", len(all_trades))

    if open_trades:
        df_open = pd.DataFrame(open_trades)
        show_cols = ['code', 'name', 'entry_price', 'entry_date', 'market_regime', 'pnl_pct']
        existing_cols = [c for c in show_cols if c in df_open.columns]
        st.dataframe(df_open[existing_cols], use_container_width=True, hide_index=True)
    else:
        st.info("当前无持仓")

    st.markdown("---")
    st.markdown("### 📜 历史交易记录")

    if all_trades:
        df_hist = pd.DataFrame(all_trades[:20])  # 最近20笔
        show_cols = ['entry_date', 'code', 'name', 'exit_date', 'pnl_pct', 'exit_reason', 'market_regime']
        existing_cols = [c for c in show_cols if c in df_hist.columns]
        st.dataframe(df_hist[existing_cols], use_container_width=True, hide_index=True)
    else:
        st.info("暂无历史交易记录")


# ============================================================
# Tab 4: 基准指标
# ============================================================
with tab_benchmark:
    st.markdown("### 📋 V2 策略回测基准指标")

    st.markdown("**基准（无过滤，27只精选股）**")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    metrics = [
        ("总交易数", V2_BENCHMARK["总交易数"]),
        ("胜率", V2_BENCHMARK["胜率"]),
        ("均值", V2_BENCHMARK["均值"]),
        ("盈亏比", V2_BENCHMARK["盈亏比"]),
        ("最大回撤", V2_BENCHMARK["最大回撤"]),
        ("总收益", V2_BENCHMARK["总收益"]),
    ]
    for col, (k, v) in zip([col1, col2, col3, col4, col5, col6], metrics):
        col.metric(k, v)

    st.markdown(f"*{V2_BENCHMARK['说明']}*")

    st.markdown("---")
    st.markdown("**推荐参数（相对强度阈值 1%，精选股池）**")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    metrics2 = [
        ("总交易数", V2_BENCHMARK_1PCT["总交易数"]),
        ("胜率", V2_BENCHMARK_1PCT["胜率"]),
        ("均值", V2_BENCHMARK_1PCT["均值"]),
        ("盈亏比", V2_BENCHMARK_1PCT["盈亏比"]),
        ("最大回撤", V2_BENCHMARK_1PCT["最大回撤"]),
        ("总收益", V2_BENCHMARK_1PCT["总收益"]),
    ]
    for col, (k, v) in zip([col1, col2, col3, col4, col5, col6], metrics2):
        col.metric(k, v)

    st.markdown(f"*{V2_BENCHMARK_1PCT['说明']}*")

    st.markdown("---")
    st.markdown("**退出方式分布（基准）**")
    exit_data = [
        ("timeout（20天到期）", "90%", "主要利润来源", "✅"),
        ("trailing_stop（移动止损）", "36%", "保护利润", "✅"),
        ("trend_break（EMA21破坏）", "3%", "主要亏损来源", "⚠️"),
    ]
    df_exit = pd.DataFrame(exit_data, columns=["退出方式", "胜率", "解读", "状态"])
    st.dataframe(df_exit, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("**券商连接状态**")
    try:
        from lib.broker import TDXBroker
        st.info("📡 TDXBroker 已加载。需要通达信客户端运行并开启交易 API（设置→个人中心→网络安全→允许网络写入）。连接地址：http://127.0.0.1:10092")
    except Exception as e:
        st.error(f"券商模块加载失败: {e}")


if __name__ == "__main__":
    pass
