"""ML 数据管道 —— 构建日频截面面板数据，供 XGBRanker/LGBMRanker 训练使用

核心流程：
  1. 批量获取个股日线数据，计算因子时间序列
  2. 计算超额收益标签（相对基准指数）
  3. 严格时间序列分割（无数据泄露）
  4. 分离特征/目标/分组信息

依赖: pandas, numpy, akshare, pyarrow
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import akshare as ak
import numpy as np
import pandas as pd

from factors import (
    calc_rsi,
    safe_float,
)
from scanner import get_stock_hist_multi_source

logger = logging.getLogger(__name__)

# ========== 常量 ==========

FACTOR_COLS: List[str] = [
    # 技术因子
    "rsi_14",
    "momentum_5",
    "momentum_20",
    "volatility_20",
    "volume_ratio",
    "trend_strength",
    "kdj_j",
    "adx",
    "cci",
    "williams_r",
    "mfi",
    "trix",
    # Barra 风格因子（需截面计算或基本面数据）
    "pe_percentile",
    "log_market_cap",
    "turnover_rate",
    # 基准因子
    "benchmark_ret_5d",
    "benchmark_ret_20d",
]

LABEL_COLS: List[str] = [
    "excess_5d",
    "excess_10d",
    "excess_20d",
]

LOOKBACK_DAYS: int = 3000  # 获取的历史天数（≈12年，覆盖 2015-至今）
MIN_HISTORY: int = 60     # 最少所需交易日（因子计算下限）
NUM_TRADING_DAYS = 252    # A股年化交易天数


class MLDataPipeline:
    """ML 数据管道

    构建日频截面面板，每条记录 = 某日某股票的因子值 + 未来超额收益标签。

    Parameters
    ----------
    codes : list
        A 股股票代码列表，如 ["000001", "600519", ...]
    benchmark_code : str
        基准指数代码，默认 "000300"（沪深 300）
    cache_path : str
        面板缓存路径（parquet 格式），默认 "cache/ml_panel.parquet"
    """

    def __init__(
        self,
        codes: list,
        benchmark_code: str = "000300",
        cache_path: str = "cache/ml_panel.parquet",
    ):
        self.codes = list(codes)
        self.benchmark_code = benchmark_code
        self.cache_path = Path(cache_path)
        self._benchmark_df: Optional[pd.DataFrame] = None
    def add_cross_sectional_features(self, panel: pd.DataFrame) -> pd.DataFrame:
        """为面板添加截面百分位因子（按日期分组，各因子的截面排名分位）"""
        df = panel.copy()
        factor_cols = [c for c in FACTOR_COLS if c in df.columns]
        for col in factor_cols:
            rank_col = col + '_rank'
            df[rank_col] = df.groupby('date')[col].rank(pct=True).fillna(0.5)
        logger.info(f'Added {len(factor_cols)} cross-sectional percentile factors')
        return df


    # ========== 基准指数数据 ==========

    def _get_benchmark(self) -> pd.DataFrame:
        """获取基准指数日线数据

        用 akshare 拉取沪深 300（sh000300）日线。

        Returns
        -------
        pd.DataFrame
            含列: date, close（已排序）
        """
        if self._benchmark_df is not None:
            return self._benchmark_df

        symbol = f"sh{self.benchmark_code}"
        try:
            df = ak.stock_zh_index_daily(symbol=symbol)
            if df.empty:
                logger.warning(f"基准指数数据为空: {symbol}")
                return pd.DataFrame()

            # 列名标准化
            df = df.rename(columns={
                "date": "date",
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            self._benchmark_df = df
            logger.info(
                f"基准指数 {symbol}: {df['date'].min()} ~ {df['date'].max()}, "
                f"{len(df)} 个交易日"
            )
            return df
        except Exception as e:
            logger.error(f"获取基准指数失败 {symbol}: {e}")
            return pd.DataFrame()

    # ========== 因子时间序列计算（批量版，避免逐日调用 compute_all_signals） ==========

    @staticmethod
    def _adx_series(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """ADX 时间序列（返回 full Series）"""
        plus_dm = high.diff()
        minus_dm = -low.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
        tr = pd.concat(
            [
                high - low,
                (high - close.shift(1)).abs(),
                (low - close.shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(period).mean()
        atr_safe = atr.replace(0, np.nan)
        plus_di = 100.0 * plus_dm.rolling(period).mean() / atr_safe
        minus_di = 100.0 * minus_dm.rolling(period).mean() / atr_safe
        di_sum = (plus_di + minus_di).replace(0, np.nan)
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        adx = dx.rolling(period).mean()
        return adx

    @staticmethod
    def _cci_series(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
    ) -> pd.Series:
        """CCI 时间序列（返回 full Series）"""
        tp = (high + low + close) / 3.0
        tp_rolling = tp.rolling(period)
        tp_sma = tp_rolling.mean()
        mad = (tp_rolling.apply(lambda x: np.abs(x - x.mean()).mean(), raw=True))
        denom = 0.015 * mad.replace(0, np.nan)
        cci = (tp - tp_sma) / denom
        return cci

    @staticmethod
    def _williams_r_series(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """Williams %%R 时间序列（返回 full Series）"""
        hh = high.rolling(period).max()
        ll = low.rolling(period).min()
        denom = hh - ll
        wr = ((hh - close) / denom.replace(0, np.nan)) * -100.0
        return wr

    @staticmethod
    def _mfi_series(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """MFI 时间序列（返回 full Series）"""
        tp = (high + low + close) / 3.0
        mf = tp * volume
        tp_diff = tp.diff()
        pos_roll = (mf.where(tp_diff > 0, 0.0)).rolling(period).sum()
        neg_roll = (mf.where(tp_diff < 0, 0.0)).rolling(period).sum()
        neg_safe = neg_roll.replace(0, np.nan)
        mfi = 100.0 - (100.0 / (1.0 + pos_roll / neg_safe))
        return mfi

    @staticmethod
    def _trix_series(close: pd.Series, period: int = 12) -> pd.Series:
        """TRIX 时间序列（返回 full Series）"""
        e1 = close.ewm(span=period, adjust=False).mean()
        e2 = e1.ewm(span=period, adjust=False).mean()
        e3 = e2.ewm(span=period, adjust=False).mean()
        prev = e3.shift(1)
        trix = (e3 - prev) / prev.replace(0, np.nan) * 100.0
        return trix

    def _compute_factor_series(self, hist: pd.DataFrame) -> dict:
        """对一只股票的完整历史，批量计算所有因子时间序列

        Parameters
        ----------
        hist : pd.DataFrame
            含列: date, open, close, high, low, volume, amount, turnover

        Returns
        -------
        dict[str, pd.Series]
            因子名 -> 与 hist 等长的 Series
        """
        close = hist["close"]
        high = hist["high"]
        low = hist["low"]
        volume = hist["volume"]
        amount = hist.get("amount", pd.Series(np.nan, index=hist.index))
        turnover = hist.get("turnover", pd.Series(np.nan, index=hist.index))

        result = {}

        # --- 可直接按列计算的因子 ---
        result["rsi_14"] = calc_rsi(close, 14)

        close_pct = close.pct_change()
        result["momentum_5"] = close.pct_change(5) * 100.0
        result["momentum_20"] = close.pct_change(20) * 100.0
        result["volatility_20"] = close_pct.rolling(20).std() * np.sqrt(NUM_TRADING_DAYS) * 100.0

        vol5 = volume.rolling(5).mean()
        vol20 = volume.rolling(20).mean()
        result["volume_ratio"] = vol5 / vol20.replace(0, np.nan)

        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        result["trend_strength"] = (ma5 / ma20.replace(0, np.nan) - 1.0) * 100.0

        # --- KDJ ---
        low_n_kdj = low.rolling(9).min()
        high_n_kdj = high.rolling(9).max()
        denom_kdj = high_n_kdj - low_n_kdj
        rsv = ((close - low_n_kdj) / denom_kdj.replace(0, np.nan)) * 100.0
        k = rsv.ewm(com=2, adjust=False).mean()
        d = k.ewm(com=2, adjust=False).mean()
        result["kdj_j"] = 3.0 * k - 2.0 * d

        # --- ADX, CCI, Williams %R, MFI, TRIX ---
        result["adx"] = self._adx_series(high, low, close)
        result["cci"] = self._cci_series(high, low, close)
        result["williams_r"] = self._williams_r_series(high, low, close)
        if volume.notna().sum() > 30:
            result["mfi"] = self._mfi_series(high, low, close, volume)
        else:
            result["mfi"] = pd.Series(np.nan, index=hist.index)
        result["trix"] = self._trix_series(close)

        # --- 基础数据 ---
        result["turnover_rate"] = turnover.astype(float)

        # --- log_market_cap（用成交额做规模代理） ---
        log_mc = np.log(amount.replace(0, np.nan))
        result["log_market_cap"] = log_mc

        # --- pe_percentile（无基本面数据，留空） ---
        result["pe_percentile"] = pd.Series(np.nan, index=hist.index)

        return result

    # ========== 单个股票处理 ==========

    def _compute_forward_returns(self, close: np.ndarray) -> dict:
        """计算多周期前视收益率

        Parameters
        ----------
        close : np.ndarray
            收盘价数组（按时间升序）

        Returns
        -------
        dict[str, np.ndarray]
            fwd_5d / fwd_10d / fwd_20d -> 各位置的前视收益
        """
        n = len(close)
        out = {}
        for label, offset in [("fwd_5d", 5), ("fwd_10d", 10), ("fwd_20d", 20)]:
            arr = np.full(n, np.nan)
            for i in range(n - offset):
                arr[i] = (close[i + offset] / close[i]) - 1.0
            out[label] = arr
        return out

    def _process_single_stock(
        self, code: str
    ) -> pd.DataFrame:
        """处理单只股票：获取历史 → 批量计算因子 → 组装逐日行

        Parameters
        ----------
        code : str
            股票代码

        Returns
        -------
        pd.DataFrame
            该股票在面板中所有日期的行，列包含: date, code, close, volume,
            FACTOR_COLS 各列, fwd_5d, fwd_10d, fwd_20d
        """
        try:
            hist = get_stock_hist_multi_source(code, days=LOOKBACK_DAYS, source="auto")
        except Exception:
            logger.debug(f"{code}: 数据获取失败")
            return pd.DataFrame()
        if hist.empty or len(hist) < MIN_HISTORY:
            logger.debug(f"{code}: 数据不足 ({len(hist) if not hist.empty else 0})")
            return pd.DataFrame()

        hist = hist.sort_values("date").reset_index(drop=True)

        # 批量计算全部因子时间序列（O(1) 次计算，非 O(days)）
        factor_series = self._compute_factor_series(hist)

        # 前视收益率
        close_arr = hist["close"].values
        fwd = self._compute_forward_returns(close_arr)

        n = len(hist)
        rows = []

        for t in range(MIN_HISTORY, n):
            date = hist.iloc[t]["date"]
            close_val = hist.iloc[t]["close"]
            volume_val = hist.iloc[t]["volume"]

            row = {
                "date": date,
                "code": code,
                "close": close_val,
                "volume": volume_val,
            }

            # 因子值（取 t 时刻的值）
            for col in FACTOR_COLS:
                # benchmark_ret 和 excess 由 build_panel 统一计算
                if col in ("benchmark_ret_5d", "benchmark_ret_20d"):
                    continue
                if col in factor_series:
                    val = factor_series[col].iloc[t]
                    row[col] = safe_float(val, np.nan) if pd.notna(val) else np.nan
                else:
                    row[col] = np.nan

            # 前视收益
            for label in ("fwd_5d", "fwd_10d", "fwd_20d"):
                val = fwd[label][t]
                row[label] = val if not np.isnan(val) else np.nan

            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df_stock = pd.DataFrame(rows)
        # 确保日期列是 datetime
        df_stock["date"] = pd.to_datetime(df_stock["date"])
        return df_stock

    # ========== 截面因子与基准对齐 ==========

    def _add_benchmark_features(
        self, panel: pd.DataFrame, bench_close: pd.Series
    ) -> pd.DataFrame:
        """添加基准指数历史收益特征

        benchmark_ret_5d / benchmark_ret_20d 表示截至当日基准指数的过去回报。
        """
        bench_close = bench_close.sort_index()
        bench_ret_5d = bench_close.pct_change(5)
        bench_ret_20d = bench_close.pct_change(20)

        panel["benchmark_ret_5d"] = panel["date"].map(bench_ret_5d)
        panel["benchmark_ret_20d"] = panel["date"].map(bench_ret_20d)
        return panel

    def _compute_excess_returns(
        self, panel: pd.DataFrame, bench_close: pd.Series
    ) -> pd.DataFrame:
        """计算超额收益标签

        excess_Nd = 股票前视N日收益 - 基准前视N日收益
        """
        bench = bench_close.sort_index()
        bench_fwd_5d = bench.pct_change(5).shift(-5)  # forward return: series[i+5]/series[i]-1
        bench_fwd_10d = bench.pct_change(10).shift(-10)
        bench_fwd_20d = bench.pct_change(20).shift(-20)

        panel["benchmark_fwd_5d"] = panel["date"].map(bench_fwd_5d)
        panel["benchmark_fwd_10d"] = panel["date"].map(bench_fwd_10d)
        panel["benchmark_fwd_20d"] = panel["date"].map(bench_fwd_20d)

        for label in ("5d", "10d", "20d"):
            bf = f"benchmark_fwd_{label}"
            sf = f"fwd_{label}"
            ef = f"excess_{label}"
            panel[ef] = panel[sf] - panel[bf]

        return panel

    # ========== 主入口：构建面板 ==========

    def build_panel(self, force_rebuild: bool = False) -> pd.DataFrame:
        """构建日频截面面板数据

        遍历股票列表，对每只股票批量计算因子时间序列，按日组装截面行，
        最终与基准数据对齐计算超额收益标签。

        Parameters
        ----------
        force_rebuild : bool
            是否强制重新构建（忽略缓存）

        Returns
        -------
        pd.DataFrame
            面板数据，列含: date, code, close, volume,
            FACTOR_COLS + LABEL_COLS 各列
        """
        # 尝试加载缓存
        if not force_rebuild and self.cache_path.exists():
            logger.info(f"加载面板缓存: {self.cache_path}")
            panel = pd.read_parquet(self.cache_path)
            logger.info(
                f"缓存面板: {len(panel)} 行, {panel['date'].nunique()} 个交易日, "
                f"{panel['code'].nunique()} 只股票"
            )
            return panel

        # 获取基准指数
        benchmark = self._get_benchmark()
        if benchmark.empty:
            logger.warning("基准指数数据为空，超额收益标签不可用")
            bench_close = pd.Series(dtype=float)
        else:
            bench_close = benchmark.set_index("date")["close"]

        logger.info(
            f"开始构建面板: {len(self.codes)} 只股票, "
            f"每个约 {LOOKBACK_DAYS} 天历史数据"
        )

        all_chunks: list[pd.DataFrame] = []
        processed_count = 0
        skip_count = 0

        for i, code in enumerate(self.codes):
            try:
                df_stock = self._process_single_stock(code)
            except Exception as e:
                logger.warning(f"{code}: 处理失败 — {e}")
                skip_count += 1
                continue

            if df_stock.empty:
                skip_count += 1
            else:
                all_chunks.append(df_stock)
                processed_count += 1

            # 每 50 只股票日志
            if (i + 1) % 50 == 0:
                total_rows = sum(len(c) for c in all_chunks)
                logger.info(
                    f"进度: {i + 1}/{len(self.codes)} 只股票, "
                    f"成功 {processed_count}, 跳过 {skip_count}, "
                    f"已收集 {total_rows} 行"
                )

        if not all_chunks:
            logger.error("面板为空：无有效股票数据")
            return pd.DataFrame()

        # 合并所有股票
        logger.info(f"合并 {len(all_chunks)} 个股票块...")
        panel = pd.concat(all_chunks, ignore_index=True)
        logger.info(f"合并完成: {len(panel)} 行")

        # 按日期排序
        panel = panel.sort_values(["date", "code"]).reset_index(drop=True)

        # 添加基准特征
        if not benchmark.empty:
            panel = self._add_benchmark_features(panel, bench_close)
        else:
            panel["benchmark_ret_5d"] = np.nan
            panel["benchmark_ret_20d"] = np.nan

        # 计算超额收益标签
        if not benchmark.empty:
            panel = self._compute_excess_returns(panel, bench_close)
        else:
            for label in ("5d", "10d", "20d"):
                panel[f"excess_{label}"] = panel[f"fwd_{label}"]

        # 删除临时列
        tmp_cols = [
            "benchmark_fwd_5d",
            "benchmark_fwd_10d",
            "benchmark_fwd_20d",
            "fwd_5d",
            "fwd_10d",
            "fwd_20d",
        ]
        panel = panel.drop(columns=[c for c in tmp_cols if c in panel.columns])

        # 缓存
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(self.cache_path, index=False)

        n_stocks = panel["code"].nunique()
        n_dates = panel["date"].nunique()
        logger.info(
            f"面板已保存: {self.cache_path} — "
            f"{len(panel)} 行, {n_dates} 个交易日, {n_stocks} 只股票, "
            f"{len(panel.columns)} 列"
        )
        return panel

    # ========== 数据分割 ==========

    def train_val_test_split(
        self, train_ratio: float = 0.7, val_ratio: float = 0.15
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """严格时间序列训练/验证/测试分割

        按日期排序后分割，**不 shuffle**，确保无数据泄露。
        断言验证各区间无日期重叠。

        Parameters
        ----------
        train_ratio : float
            训练集比例（按交易日计数），默认 0.7
        val_ratio : float
            验证集比例，默认 0.15
            （测试集比例 = 1 - train_ratio - val_ratio）

        Returns
        -------
        tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
            train, val, test
        """
        if not self.cache_path.exists():
            raise FileNotFoundError(
                f"面板缓存不存在: {self.cache_path}，请先调用 build_panel()"
            )

        panel = pd.read_parquet(self.cache_path)
        if panel.empty:
            raise ValueError("面板为空")

        dates = sorted(panel["date"].unique())
        n_dates = len(dates)

        if n_dates < 10:
            raise ValueError(f"交易日数不足 ({n_dates})，无法分割")

        n_train = max(1, int(n_dates * train_ratio))
        n_val = max(1, int(n_dates * val_ratio))
        n_test = n_dates - n_train - n_val

        if n_test < 1:
            logger.warning(
                f"测试集为 0 行 (n_dates={n_dates}, train={n_train}, val={n_val})，"
                f"调整为 val 分 1 天给 test"
            )
            n_val = max(1, n_val - 1)
            n_test = 1

        train_dates = dates[:n_train]
        val_dates = dates[n_train:n_train + n_val]
        test_dates = dates[n_train + n_val:]

        train = panel[panel["date"].isin(train_dates)].copy()
        val = panel[panel["date"].isin(val_dates)].copy()
        test = panel[panel["date"].isin(test_dates)].copy()

        # 断言：无日期重叠
        assert len(set(train_dates) & set(val_dates)) == 0, "训练/验证集日期重叠"
        assert len(set(train_dates) & set(test_dates)) == 0, "训练/测试集日期重叠"
        assert len(set(val_dates) & set(test_dates)) == 0, "验证/测试集日期重叠"

        logger.info(
            f"时间序列分割: "
            f"train {len(train)} 行 ({len(train_dates)} 日, "
            f"{train_dates[0].date()} ~ {train_dates[-1].date()}), "
            f"val {len(val)} 行 ({len(val_dates)} 日, "
            f"{val_dates[0].date()} ~ {val_dates[-1].date()}), "
            f"test {len(test)} 行 ({len(test_dates)} 日, "
            f"{test_dates[0].date()} ~ {test_dates[-1].date()})"
        )

        return train, val, test

    # ========== 特征/目标/分组分离 ==========

    def feature_target_split(
        self, df: pd.DataFrame, target_col: str = "excess_5d"
    ) -> Tuple[pd.DataFrame, pd.Series, Optional[np.ndarray]]:
        """将面板数据分离为特征、目标、分组标签

        用于 XGBRanker / LGBMRanker 等排序模型的输入准备。

        Parameters
        ----------
        df : pd.DataFrame
            面板数据（单日截面或多日均可）
        target_col : str
            目标列名，默认 "excess_5d"

        Returns
        -------
        tuple[pd.DataFrame, pd.Series, np.ndarray | None]
            X  : 特征 DataFrame（仅 FACTOR_COLS 中存在的列）
            y  : 目标 Series（NaN 样本会被过滤）
            groups : 分组标签数组（按交易日分组，用于 ranking）
                     若 df 无 date 列则返回 None
        """
        available_factors = [c for c in FACTOR_COLS if c in df.columns]
        X = df[available_factors].copy()

        if target_col in df.columns:
            y = df[target_col].copy()
        else:
            raise KeyError(
                f"目标列 '{target_col}' 不在 DataFrame 中。"
                f"可用列: {list(df.columns)}"
            )

        # 分组标签（qids for ranking models）
        groups: Optional[np.ndarray] = None
        if "date" in df.columns:
            unique_dates = sorted(df["date"].unique())
            date_to_group = {d: i for i, d in enumerate(unique_dates)}
            groups = np.array(df["date"].map(date_to_group), dtype=np.int32)

        # 过滤 NaN 目标
        valid_mask = y.notna()
        n_dropped = (~valid_mask).sum()
        if n_dropped > 0:
            logger.debug(
                f"删除 {n_dropped} 行（目标值为 NaN），"
                f"{valid_mask.sum()} 行保留"
            )
            X = X[valid_mask].copy()
            y = y[valid_mask].copy()
            if groups is not None:
                groups = groups[valid_mask]

        return X, y, groups


# ========== 快速验证入口 ==========

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 仅测试代码正确性，不实际拉数据
    print("MLDataPipeline 导入成功")
    print(f"因子列数: {len(FACTOR_COLS)}")
    print(f"标签列数: {len(LABEL_COLS)}")
