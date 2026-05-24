"""V4 私人交易终端 Dashboard（Streamlit）

使用方式：
    streamlit run dashboard.py

依赖：
    pip install streamlit pandas

V4 Dashboard 显示：
    - 当前市场状态（HS300 四态）
    - 仓位建议范围
    - 今日观察池（ML 候选股）
    - 风控提示
    - 各策略统计
"""
import streamlit as st
import pandas as pd
from datetime import datetime

from reporter.report_generator import build_v4_dashboard_report, format_v4_report_text


def render_market_status(report: dict):
    """渲染市场状态卡片"""
    regime = report.get("市场状态", {})
    status = regime.get("状态", "UNKNOWN")
    confidence = regime.get("置信度", "0%")
    position_range = regime.get("仓位范围", "0%-0%")

    # 颜色映射
    color_map = {
        "TREND_UP": "green",
        "MEAN_REVERSION": "blue",
        "COMPRESSION": "orange",
        "TREND_DOWN": "red",
    }
    color = color_map.get(status, "gray")

    st.markdown(f"""
    ### 市场状态
    <span style="color:{color};font-size:2em;font-weight:bold">{status}</span>

    - 置信度: {confidence}
    - 仓位范围: {position_range}
    - 说明: {regime.get("说明", "")}
    """, unsafe_allow_html=True)


def render_observation_pool(report: dict):
    """渲染观察池"""
    pool = report.get("观察池", [])
    if not pool:
        st.info("当前无观察池候选股")
        return

    df = pd.DataFrame(pool)
    df = df[["代码", "名称", "ML评分", "调整原因"]]
    df["ML评分"] = df["ML评分"].apply(lambda x: f"{x:.2f}")
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_risk_alerts(report: dict):
    """渲染风控提示"""
    alerts = report.get("风控提示", [])
    if not alerts:
        st.success("当前无风控告警")
        return

    for alert in alerts:
        st.warning(f"**{alert['rule']}**: {alert['message']}")


def render_strategy_summary(report: dict):
    """渲染策略统计"""
    stats = report.get("策略统计", {})
    if not stats:
        st.info("无策略数据")
        return

    df = pd.DataFrame(list(stats.items()), columns=["策略", "候选股数"])
    st.dataframe(df, use_container_width=True, hide_index=True, bar_chart=False)


def main():
    st.set_page_config(
        page_title="V4 私人交易终端",
        page_icon="📊",
        layout="wide",
    )

    st.title("📊 V4 私人交易终端")
    st.caption(f"更新时间: {report.get('生成时间', datetime.now().strftime('%Y-%m-%d %H:%M'))}")

    # 侧边栏：使用说明
    with st.sidebar:
        st.header("使用说明")
        st.markdown("""
        **V4 框架说明**：
        - TREND_UP: 积极做多 (75%-100%)
        - MEAN_REVERSION: 短线回归 (25%-50%)
        - COMPRESSION: 少做等突破 (0%-25%)
        - TREND_DOWN: 禁止开仓 (0%)
        """)
        st.markdown("---")
        st.markdown("**数据来源**: HS300 指数日线")
        st.markdown("**更新频率**: 每日收盘后")

    # 主区域
    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown("### 市场状态")
        # 这里需要传入真实的 scan_result
        st.info("运行 `run_all_strategies()` 后传入 report 即可显示")

    with col2:
        st.markdown("### 策略统计")
        st.info("运行 `run_all_strategies()` 后传入 report 即可显示")

    st.markdown("---")
    st.markdown("### 观察池")
    st.info("运行 `run_all_strategies()` 后传入 report 即可显示")

    st.markdown("---")
    st.markdown("### 风控提示")
    st.info("运行 `run_all_strategies()` 后传入 report 即可显示")


if __name__ == "__main__":
    main()
