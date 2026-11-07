"""TDX 市场统计数据（ScJyData_zbca.csv）"""
import pandas as pd
from pathlib import Path
from datetime import datetime

DS_CSV_PATH = Path("D:/TDX/vipdoc/ds/ScJyData_zbca.csv")

def get_market_stats(days: int = 30) -> pd.DataFrame:
    """读取市场统计数据（按日期倒序）"""
    if not DS_CSV_PATH.exists():
        return pd.DataFrame()
    df = pd.read_csv(DS_CSV_PATH)
    df['date'] = pd.to_datetime(df['date'], format='%Y_%m_%d')
    if len(df) > days:
        df = df.head(days)
    return df

def get_latest_stats() -> dict:
    """获取最新一天的市场统计数据"""
    df = get_market_stats(days=1)
    if df.empty:
        return {}
    row = df.iloc[0]
    return {col: row[col] for col in df.columns}
