"""WeStock Data 数据源封装

来源：WeStock Data skill（腾讯自选股 API）。返回 Markdown 表格 → 解析为 DataFrame。

双 CLI 通道（2026-11-22 核验后确立）：
- **npm CLI**（``westock-data-clawhub@1.0.4``）：生产 K 线/财务/资金流/技术指标已实测可用，
  继续用于 registry 源栈（P-125 复权口径已钉死，不迁移）。
- **Go CLI**（``westock`` v0.0.11）：命令体系完整（quote/news/report/notice/sector/index/
  macro/fund north+south/etf/hot），覆盖 npm CLI 不支持的能力；需专属 APIKey 鉴权。

命令可用性依据：scripts/_verify_westock_*.py 于 2026-11-22 端到端实测
（npm quote 渠道不可用 / hot 需子命令 / lhb 需股票码）。
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


def _resolve_go_cmd() -> List[str]:
    """解析 Go CLI 命令（WESTOCK_GO_CLI 环境变量优先，否则 shutil.which 解析 westock）。

    与 npm CLI 并存：Go CLI 命令体系更新（quote/news/report/notice/sector/index/macro/
    fund north/south/etf/hot），npm CLI 不支持这些命令；但 Go CLI 需专属 APIKey 鉴权，
    未鉴权时调用返回空串，由上层按「能力不可用」降级，不阻塞 npm 生产链路。
    """
    override = os.environ.get("WESTOCK_GO_CLI", "").strip()
    if override:
        return [override]
    go = shutil.which("westock")
    return [go] if go else []


GO_CMD = _resolve_go_cmd()


_AUTH_URL_RE = re.compile(r"AUTH_REQUIRED:\S+")
_SECRET_PARAM_RE = re.compile(r"(?i)(code|token|key|secret)=[^&\s]+")


def _sanitize_go_error(text: str) -> str:
    """脱敏 CLI 错误：AUTH_REQUIRED 后面是一次性授权 URL（含 code=），不得进日志。"""
    text = _AUTH_URL_RE.sub("AUTH_REQUIRED:<redacted>", text or "")
    text = _SECRET_PARAM_RE.sub(r"\1=<redacted>", text)
    return " ".join(text.split())[:200]


def _run_go(args: List[str], timeout: int = 30) -> str:
    """执行 Go CLI 命令；未安装或未鉴权返回空串（记 warning 不抛错，保持降级语义）。"""
    if not GO_CMD:
        logger.debug("WeStock Go CLI 未安装（shutil.which westock 为空），跳过")
        return ""
    try:
        result = subprocess.run(
            GO_CMD + args, capture_output=True, text=True, timeout=timeout, encoding="utf-8"
        )
        if result.returncode != 0:
            logger.warning("WeStock Go 命令失败: %s",
                           _sanitize_go_error(result.stderr or result.stdout))
            return ""
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning(f"WeStock Go 命令超时: {' '.join(args[:3])}")
        return ""
    except FileNotFoundError:
        logger.warning("WeStock Go CLI 路径失效，跳过")
        return ""


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


def _to_index_code(code: str) -> str:
    """指数代码转换——与个股规则不同，不能复用 _to_westock_code。

    上交所指数 000xxx/880xxx → sh；深交所指数 399xxx → sz。
    实测：沪深300 = sh000300（若按个股规则会错成 sz000300）。
    """
    code = str(code).strip()
    if not _CODE_RE.match(code):
        raise ValueError(f"invalid index code: {code!r}")
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith("399"):
        return f"sz{code}"
    if code.startswith(("000", "880")):
        return f"sh{code}"
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


def _parse_markdown_table(output: str, table_index: int = 0) -> pd.DataFrame:
    """解析 Markdown 表格为 DataFrame，默认取第一张表（主表）。

    P-127（2026-11-22）：旧实现用 ``[c for c in split('|') if c.strip()]`` 过滤空单元格，
    合法含空字段的行（quote.price_currency='' 等）因列数不足被静默丢弃。

    P-128（2026-11-22）：修完 P-127 后又发现 CLI 单条输出可能含**多张表**——
    ``etf profile`` 主表之后跟着「资产配置」「基金经理履历」等附表（非 `|` 文本行分隔，
    列数各异）。旧实现把所有 `|` 行当一张表 → 附表被误判为列数不符，刷 warning。
    根因修：按**连续 `|` 行块**切分表格，默认只解析 table_index 指定的那张，
    不同表之间不再混判；单表输出行为不变。
    """
    if not output or "|" not in output:
        return pd.DataFrame()

    def _cells(line: str) -> List[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    # 按连续 '|' 行切块：非 '|' 行（标题/空行/说明）是表格边界
    blocks: List[List[str]] = []
    current: List[str] = []
    for raw in output.split("\n"):
        if raw.strip().startswith("|"):
            current.append(raw.strip())
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    if table_index >= len(blocks):
        return pd.DataFrame()
    lines = blocks[table_index]
    if len(lines) < 2:  # 单张表至少要有表头 + 分隔行
        return pd.DataFrame()

    header = _cells(lines[0])
    # P-132：第三方输出的新闻标题含未转义 '|'（如「晚报|刚刚...」），该行多出列被
    # 整行丢弃。若存在文本型 title 列且某行恰好多出若干列，把溢出部分并回 title
    # （title 的位置与后续列边界由 header 固定，可精确复原）。
    text_cols = [i for i, h in enumerate(header) if h.lower() in ("title", "news_title", "name", "summary")]
    data = []
    for line in lines[2:]:  # 跳过表头和分隔行
        cols = _cells(line)
        if len(cols) == len(header):
            data.append(cols)
        elif len(cols) > len(header) and text_cols:
            tc = text_cols[0]
            extra = len(cols) - len(header)
            merged = cols[:tc]  # title 之前的列不动
            merged.append("|".join(cols[tc:tc + 1 + extra]))  # title 吞掉溢出段
            merged += cols[tc + 1 + extra:]  # title 之后的列顺延
            if len(merged) == len(header):
                data.append(merged)
                continue
            logger.warning(f"WeStock 表格行合并后仍不符 (期望 {len(header)})，跳过: {line[:80]}")
        else:
            logger.warning(
                f"WeStock 表格行列数不符 (期望 {len(header)}, 实际 {len(cols)})，跳过: {line[:80]}"
            )
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
    """获取实时行情（Go CLI quote；npm CLI 该命令已失效，见核验记录 2026-11-22）。"""
    wc = _to_westock_code(code)
    df = _parse_markdown_table(_run_go(["quote", wc]))
    if df.empty:
        return {}
    return df.iloc[0].to_dict()


def get_hot(kind: str = "stock", limit: int = 50) -> pd.DataFrame:
    """获取热搜榜。kind: stock/news/board/etf（Go CLI hot 子命令）。

    npm CLI 的 ``hot`` 缺省参数不成立（实测返回空），必须带子命令；
    这里统一走 Go CLI ``hot <kind>``，与 SKILL 命令体系一致。
    """
    if kind not in ("stock", "news", "board", "etf"):
        raise ValueError(f"invalid hot kind: {kind!r}")
    output = _run_go(["hot", kind, "--limit", str(limit)])
    return _parse_markdown_table(output)


def get_lhb(code: str, date: str = None) -> pd.DataFrame:
    """获取个股龙虎榜。code 必填——npm CLI ``lhb`` 不带股票码直接报错（实测）。"""
    wc = _to_westock_code(code)
    args = ["lhb", wc]
    if date:
        args += ["--date", date]
    output = _run_westock(args)
    return _parse_markdown_table(output)


# ── Go CLI 专属能力（npm CLI 无对应命令）─────────────────────

def get_news(code: str, limit: int = 20, mode: str = "list") -> pd.DataFrame:
    """个股新闻/研报/公告。mode: list|detail；type 0公告 1研报 2新闻 3全部。"""
    wc = _to_westock_code(code)
    output = _run_go(["news", mode, wc, "--limit", str(limit)])
    return _parse_markdown_table(output)


def get_report(code: str, limit: int = 20) -> pd.DataFrame:
    """卖方研报列表。"""
    wc = _to_westock_code(code)
    return _parse_markdown_table(_run_go(["report", "list", wc, "--limit", str(limit)]))


def get_notice(code: str, limit: int = 20) -> pd.DataFrame:
    """公司公告列表。"""
    wc = _to_westock_code(code)
    return _parse_markdown_table(_run_go(["notice", "list", wc, "--limit", str(limit)]))


def get_sector_rank(kind: str = "industry", limit: int = 50) -> pd.DataFrame:
    """板块行情榜。kind: industry|concept。"""
    if kind not in ("industry", "concept"):
        raise ValueError(f"invalid sector kind: {kind!r}")
    return _parse_markdown_table(_run_go(["sector", "ranking", "--kind", kind, "--limit", str(limit)]))


def get_index_constituent(code: str) -> pd.DataFrame:
    """指数成份股。指数代码用专用映射（沪深300=sh000300，非 sz000300）。"""
    wc = _to_index_code(code)
    return _parse_markdown_table(_run_go(["index", "constituent", wc]))


def get_macro(names: str, limit: int = 3) -> pd.DataFrame:
    """宏观指标。names 为指标短名（逗号分隔），先 macro list 查短名。"""
    return _parse_markdown_table(_run_go(["macro", "indicator", names, "--limit", str(limit)]))


def get_fund_north(code: str) -> pd.DataFrame:
    """北向资金持仓。"""
    wc = _to_westock_code(code)
    return _parse_markdown_table(_run_go(["fund", "north-holding", wc]))


def get_fund_south(code: str) -> pd.DataFrame:
    """南下资金持仓。"""
    wc = _to_westock_code(code)
    return _parse_markdown_table(_run_go(["fund", "south-holding", wc]))


def get_etf(code: str, aspect: str = "profile") -> pd.DataFrame:
    """ETF 多维度数据。aspect: profile/overview/holdings/nav。

    Go CLI ``etf`` 无裸调用（实测返回 Usage 提示非空表），必须带子命令。
    """
    if aspect not in ("profile", "overview", "holdings", "nav"):
        raise ValueError(f"invalid etf aspect: {aspect!r}")
    wc = _to_westock_code(code)
    return _parse_markdown_table(_run_go(["etf", aspect, wc]))


def batch_kline(codes: List[str], period: str = "day",
                limit: int = 200) -> Dict[str, pd.DataFrame]:
    """批量获取 K 线数据（逐个调用，避免废弃的批量子进程）"""
    results = {}
    for code in codes:
        df = get_kline(code, period, limit)
        if not df.empty:
            results[code] = df
    return results
