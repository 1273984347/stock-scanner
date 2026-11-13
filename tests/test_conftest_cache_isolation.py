"""P-80 机器判据（2026-11-14）：测试套件永不写生产 cache/。

事故：2026-11-13 23:50 全量测试套件运行中，真实 get_realtime_quotes 走到
pytdx 失败 → akshare（conftest autouse mock，内容是「股票N」随机价）→
_save_cache 把 mock 行情落盘到生产 cache/realtime_realtime.parquet。
40 分钟后的真实扫描在盘后 30min TTL 内加载毒缓存 ⇒ 量价突破/多因子选股
KeyError '成交量' + ML/评分吃假价格（000001 @ 81.77）。

修复：conftest autouse `_isolate_repo_cache_dirs` 把各缓存模块根目录重定向到
pytest tmp。本文件验证该隔离端到端生效 + mock 行情满足 get_realtime_quotes
docstring 的列契约（含 成交量）。
"""

import sys
from pathlib import Path

import lib.data.data_layer as data_layer


def _repo_cache_root() -> Path:
    """从模块文件位置推回仓库 cache/（不受 fixture 重定向影响）。"""
    return Path(data_layer.__file__).resolve().parent.parent.parent / "cache"


def test_cache_dir_fixture_redirects_to_tmp():
    """autouse 隔离生效：data_layer.CACHE_DIR 不再指向仓库 cache/。"""
    assert data_layer.CACHE_DIR != _repo_cache_root()
    assert _repo_cache_root() not in data_layer.CACHE_DIR.parents


def test_get_realtime_quotes_writes_tmp_not_repo(monkeypatch):
    """毒缓存事故回归钉：真实 get_realtime_quotes 落盘只进 tmp，仓库 cache/ 零写入。

    复现链：pytdx import 失败（模拟无 TDX 环境）→ akshare 兜底
    （autouse mock 返回「股票N」假行情）→ _save_cache 落盘。
    """
    # 强制 pytdx import 失败，走 akshare 兜底（与事故时环境一致）
    monkeypatch.setitem(sys.modules, "pytdx", None)

    df = data_layer.get_realtime_quotes()
    assert not df.empty, "autouse akshare mock 应返回 100 行行情"

    # mock 契约：get_realtime_quotes docstring 承诺的列（P-80 修正前缺 成交量）
    for col in ("代码", "名称", "最新价", "涨跌幅", "成交量", "成交额"):
        assert col in df.columns, f"mock 行情缺列 {col}（违反 get_realtime_quotes 契约）"

    # 落盘进 tmp 隔离区（P-80 修正前：写进生产 cache/）
    saved = data_layer.CACHE_DIR / "realtime_realtime.parquet"
    assert saved.exists(), "realtime 行情应落盘到被重定向的 tmp 缓存目录"

    # 生产缓存零污染
    assert not (_repo_cache_root() / "realtime_realtime.parquet").exists(), (
        "测试运行不得向生产 cache/ 写入 realtime_realtime.parquet（P-80 毒缓存回归）"
    )
