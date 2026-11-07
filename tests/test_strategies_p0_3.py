"""
P0-3 修复测试: 提取 _safe_signal_date() 处理盘中/盘后时序

Bug 描述 (lib/strategies.py 多处):
    7+ 处直接用 datetime.now().strftime(...) 作 akshare 查询日期或
    signal_date。盘中（09:30-15:00）调用时当天数据可能未收盘，
    paper trading 信号错位。

修复:
    提取 _safe_signal_date(now: Optional[datetime] = None) -> str
    - 接受可选 now 参数（默认 None = datetime.now()）
    - 盘未收盘（hour<15 或 hour==15 and minute<30）→ 返回上一交易日
    - 否则返回当前日期
    - 上一交易日：周一-3=周五；其他-1=昨天（跳过周末简化版）

    所有 datetime.now() 调用改用 _safe_signal_date()，
    akshare 紧凑格式（YYYYMMDD）通过 .replace("-", "") 派生。
"""
from datetime import datetime

import pytest


class TestSafeSignalDate:
    """_safe_signal_date() 时间感知测试"""

    def _call(self, iso):
        """调用 _safe_signal_date(iso 解析的 datetime)"""
        from lib.strategy.strategies import _safe_signal_date
        return _safe_signal_date(datetime.fromisoformat(iso))

    def test_monday_pre_open_returns_last_friday(self):
        """P0-3: 周一 10:00（盘前）应返回上周五"""
        # 2026-06-08 是周一
        result = self._call("2026-06-08T10:00:00")
        assert result == "2026-06-05", (
            f"周一 10:00 盘前应返回上周五 2026-06-05，实际: {result}"
        )

    def test_monday_open_instant_returns_last_friday(self):
        """P0-3: 周一 09:30（开盘瞬间）应返回上周五"""
        result = self._call("2026-06-08T09:30:00")
        assert result == "2026-06-05", (
            f"周一 09:30 开盘瞬间应返回上周五 2026-06-05，实际: {result}"
        )

    def test_monday_post_close_returns_same_day(self):
        """P0-3: 周一 16:00（盘后）应返回当天"""
        result = self._call("2026-06-08T16:00:00")
        assert result == "2026-06-08", (
            f"周一 16:00 盘后应返回当天 2026-06-08，实际: {result}"
        )

    def test_tuesday_pre_market_returns_monday(self):
        """P0-3: 周二 10:00（盘前）应返回周一"""
        # 2026-06-09 是周二
        result = self._call("2026-06-09T10:00:00")
        assert result == "2026-06-08", (
            f"周二 10:00 盘前应返回周一 2026-06-08，实际: {result}"
        )

    def test_tuesday_15_29_returns_monday(self):
        """P0-3: 15:29（盘未收稳）应返回前一交易日"""
        result = self._call("2026-06-09T15:29:00")
        assert result == "2026-06-08", (
            f"15:29 盘未收稳应返回周一 2026-06-08，实际: {result}"
        )

    def test_tuesday_15_30_returns_same_day(self):
        """P0-3: 15:30（盘收稳临界点）应返回当天"""
        result = self._call("2026-06-09T15:30:00")
        assert result == "2026-06-09", (
            f"15:30 盘收稳应返回当天 2026-06-09，实际: {result}"
        )

    def test_friday_pre_market_returns_thursday(self):
        """P0-3: 周五 10:00（盘前）应返回周四"""
        # 2026-06-12 是周五
        result = self._call("2026-06-12T10:00:00")
        assert result == "2026-06-11", (
            f"周五 10:00 盘前应返回周四 2026-06-11，实际: {result}"
        )

    def test_friday_post_close_returns_same_day(self):
        """P0-3: 周五 16:00（盘后）应返回当天"""
        result = self._call("2026-06-12T16:00:00")
        assert result == "2026-06-12", (
            f"周五 16:00 盘后应返回当天 2026-06-12，实际: {result}"
        )

    def test_default_now_works(self, monkeypatch):
        """P0-3: 默认参数（now=None）应使用 datetime.now() 不崩溃"""
        from lib.strategy.strategies import _safe_signal_date
        # 不传 now，调用应成功
        result = _safe_signal_date()
        assert isinstance(result, str)
        assert len(result) == 10  # YYYY-MM-DD
        assert result[4] == "-"
        assert result[7] == "-"

    def test_returns_iso_format(self):
        """P0-3: 返回值必须是 ISO 格式 YYYY-MM-DD（akshare 调用会基于此派生 YYYYMMDD）"""
        result = self._call("2026-06-08T16:00:00")
        # 验证格式
        assert len(result) == 10
        year, month, day = result.split("-")
        assert len(year) == 4
        assert len(month) == 2
        assert len(day) == 2


class TestStrategiesUseSafeSignalDate:
    """验证所有 datetime.now() 调用已替换为 _safe_signal_date()"""

    def test_strategies_module_no_direct_datetime_now(self):
        """P0-3: lib/strategies.py 中不应再有 datetime.now() 直接调用

        修复前：lib/strategies.py 中有 7+ 处 datetime.now().strftime(...)
        修复后：所有调用都改用 _safe_signal_date()
        """
        import re
        with open("lib/strategy/strategies.py", "r", encoding="utf-8") as f:
            source = f.read()

        # 查找所有 datetime.now() 调用（排除 _safe_signal_date 函数体内的合法调用）
        # 我们只看 _safe_signal_date 之外的 datetime.now() 调用
        func_match = re.search(
            r"def _safe_signal_date\([^)]*\)[^:]*:.*?(?=\n\ndef |\nclass |\Z)",
            source, re.DOTALL
        )
        func_body = func_match.group(0) if func_match else ""
        source_outside_func = source.replace(func_body, "")

        direct_now_calls = re.findall(r"datetime\.now\(\)", source_outside_func)
        assert len(direct_now_calls) == 0, (
            f"P0-3 修复未生效：lib/strategies.py 中仍有 {len(direct_now_calls)} 处 "
            f"datetime.now() 直接调用（应全部改用 _safe_signal_date()）"
        )

    def test_safe_signal_date_is_exported(self):
        """P0-3: _safe_signal_date 函数必须存在且可导入"""
        from lib.strategy.strategies import _safe_signal_date
        assert callable(_safe_signal_date)
