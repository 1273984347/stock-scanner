"""WeStock Data 数据源封装

来源：WeStock Data skill（腾讯自选股 API）
通过 npx CLI 调用，返回 Markdown 表格 → 解析为 DataFrame
"""
import subprocess
import pandas as pd
import io
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

WESTOCK_CMD = ["npx", "-y", "westock-data-clawhub@1.0.4"]


def _to_westock_code(code: str) -> str:
    """转换股票代码格式：000001 → sz000001, 600519 → sh600519"""
    code = code.strip()
    if code.startswith(("sh", "sz", "bj", "hk", "us")):
        return code
    if code.startswith(("6", "5")):
        return f"sh{code}"
    if code.startswith(("0", "3", "2")):
        return f"sz{code}"
    if code.startswith(("8", "4")):
        return f"bj{code}"
    return code


def _run_westock(args: List[str], timeout: int = 30) -> str:
    """执行 westock CLI 命令"""
    try:
        result = subprocess.run(
            WESTOCK_CMD + args,
            capture_output=True, text=True, timeout=timeout, encoding="utf-8"
        )
        if result.returncode != 0:
            logger.warning(f"WeStock 命令失败: {result.stderr[:200]}")
            return ""
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning(f"WeStock 命令超时: {' '.join(args[:3])}")
        return ""
    except FileNotFoundError:
        logger.error("npx 未找到，请安装 Node.js >= v18")
        return ""


def _parse_markdown_table(output: str) -> pd.DataFrame:
    """解析 Markdown 表格为 DataFrame"""
    if not output or "|" not in output:
        return pd.DataFrame()
    lines = [l.strip() for l in output.strip().split("\n") if l.strip().startswith("|")]
    if len(lines) < 3:
        return pd.DataFrame()
    # 跳过分隔行
    header = [c.strip() for c in lines[0].split("|") if c.strip()]
    data = []
    for line in lines[2:]:  # 跳过表头和分隔行
        cols = [c.strip() for c in line.split("|") if c.strip()]
        if len(cols) == len(header):
            data.append(cols)
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data, columns=header)


def get_kline(code: str, period: str = "day", limit: int = 200,
              fq: str = "qfq") -> pd.DataFrame:
    """获取 K 线数据

    Args:
        code: 股票代码（如 "000001" 或 "sz000001"）
        period: day/week/month/season/year
        limit: 数据条数（最大 2000）
        fq: qfq(前复权)/hfq(后复权)/bfq(不复权)

    Returns:
        DataFrame with columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, ...
    """
    wc = _to_westock_code(code)
    output = _run_westock(["kline", wc, "--period", period, "--limit", str(limit), "--fq", fq])
    return _parse_markdown_table(output)


def get_finance(code: str, ftype: str = "lrb", num: int = 4) -> pd.DataFrame:
    """获取财务报表

    Args:
        code: 股票代码
        ftype: lrb(利润表)/zcfz(资产负债表)/xjll(现金流量表)
        num: 期数
    """
    wc = _to_westock_code(code)
    output = _run_westock(["finance", wc, "--type", ftype, "--num", str(num)])
    return _parse_markdown_table(output)


def get_capital_flow(code: str, date: str = None) -> pd.DataFrame:
    """获取资金流向"""
    wc = _to_westock_code(code)
    args = ["asfund", wc]
    if date:
        args += ["--date", date]
    output = _run_westock(args)
    return _parse_markdown_table(output)


def get_technical(code: str) -> pd.DataFrame:
    """获取技术指标"""
    wc = _to_westock_code(code)
    output = _run_westock(["technical", wc])
    return _parse_markdown_table(output)


def get_quote(code: str) -> Dict:
    """获取实时行情"""
    wc = _to_westock_code(code)
    output = _run_westock(["quote", wc])
    df = _parse_markdown_table(output)
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def get_hot() -> pd.DataFrame:
    """获取热门股票"""
    output = _run_westock(["hot"])
    return _parse_markdown_table(output)


def get_lhb(date: str = None) -> pd.DataFrame:
    """获取龙虎榜"""
    args = ["lhb"]
    if date:
        args += ["--date", date]
    output = _run_westock(args)
    return _parse_markdown_table(output)


def batch_kline(codes: List[str], period: str = "day",
                limit: int = 200) -> Dict[str, pd.DataFrame]:
    """批量获取 K 线数据（逐个调用，避免废弃的批量子进程）"""
    results = {}
    for code in codes:
        df = get_kline(code, period, limit)
        if not df.empty:
            results[code] = df
    return results
