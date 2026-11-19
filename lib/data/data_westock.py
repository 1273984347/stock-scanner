"""WeStock Data 数据源封装

来源：WeStock Data skill（腾讯自选股 API）
通过 npx CLI 调用，返回 Markdown 表格 → 解析为 DataFrame
"""
import os
import re
import shutil
import subprocess
import pandas as pd
import io
import logging
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)


def _resolve_westock_cmd() -> List[str]:
    """解析 westock CLI 命令（WESTOCK_CLI 环境变量优先，否则 shutil.which 解析 npx）。

    Windows 坑（P-111，2026-11-20 实测）：``subprocess.run(list)`` 不走 PATHEXT，
    裸 ``"npx"`` → FileNotFoundError → 误报「npx 未找到，请安装 Node.js >= v18」——
    本机装有 Node 18+（``shutil.which("npx")`` 能解析出 npx.CMD 完整路径），
    westock 源因此全灭数月，被误诊为「缺 Node」。shutil.which 两平台都正确。
    """
    override = os.environ.get("WESTOCK_CLI", "").strip()
    if override:
        return [override]
    npx = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
    return [npx, "-y", "westock-data-clawhub@1.0.4"]


# 批次 C / P1-11：默认仍走 `npx -y`（会在运行时联网下载并执行第三方包及其依赖树）。
# 生产环境建议先 `npm i -D westock-data-clawhub@1.0.4`，再用 WESTOCK_CLI 指向本地
# 可执行文件（或 `npx --no-install`），避免运行时拉包这一供应链面。
WESTOCK_CMD = _resolve_westock_cmd()

# 只允许字母数字与点，且不允许以 '-' 开头：subprocess 已用 list（无 shell 注入），
# 这条挡的是「参数被 CLI 当成 flag」与路径穿越式拼接。
_CODE_RE = re.compile(r"^[A-Za-z0-9.]{1,12}$")


def _to_westock_code(code: str) -> str:
    """转换股票代码格式：000001 → sz000001, 600519 → sh600519"""
    code = str(code).strip()
    if not _CODE_RE.match(code):
        raise ValueError(f"invalid stock code: {code!r}")
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
              fq: str = "qfq", as_of_date: Optional[str] = None) -> pd.DataFrame:
    """获取 K 线数据

    Args:
        code: 股票代码（如 "000001" 或 "sz000001"）
        period: day/week/month/season/year
        limit: 数据条数（最大 2000）
        fq: qfq(前复权)/hfq(后复权)/bfq(不复权)
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。

    ⚠️ westock CLI 只能按"最新 N 条"取数，**不支持按日期查询**
    => 只能在返回后截断。若 CLI 取回的 N 条全部晚于 as_of_date，
       结果会**为空或少于 N 条**（少取，不是多取 ⇒ 不会引入前视偏差）。
    """
    wc = _to_westock_code(code)
    output = _run_westock(["kline", wc, "--period", period, "--limit", str(limit), "--fq", fq])
    df = _parse_markdown_table(output)
    # P-112：CLI 1.0.4 实测返回英文表头（date/open/last/...），旧版可能为中文——
    # as_of 截断若只认「日期」，英文表头下静默跳过 = 回测前视偏差，必须双形态兼容
    date_col = "日期" if "日期" in df.columns else ("date" if "date" in df.columns else None)
    if as_of_date is not None and not df.empty and date_col:
        cutoff = pd.Timestamp(as_of_date)
        dates = pd.to_datetime(df[date_col], errors="coerce")
        df = df[dates <= cutoff].copy()
    return df


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
