"""批次 B 第一件事：L3 取证 pin 与 L10 前端路径判据的机器判据。

两条门禁今晚判红都不是业务回归，而是判据自身的结构性缺陷：
- L3：`get_market_sentiment` 漏 pin，取证链路读生产缓存（G2 / P-18 / P-19 复发）；
- L10：把路径数量「36」硬编码进探针，而清单由 L7 从源码生成，数字必然漂移。

本文件锁住修复，并证明两条判据**真的会判红**，不是空判据（坑表 P-54）。
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import pathlib
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(script_name: str):
    """按项目惯例用 importlib 加载 scripts/ 下的门禁工具。"""
    spec = importlib.util.spec_from_file_location(
        f"_gate_{script_name}", ROOT / "scripts" / f"{script_name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_frontend_file(tmp_path, payload) -> pathlib.Path:
    path = tmp_path / "frontend-api-paths.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------ L3 / G2

def test_l3_market_sentiment_pin_survives_poisoned_runtime(monkeypatch):
    """把生产情绪函数换成极端负值，取证链路仍必须得到中性门控。

    若 pin 被删掉，risk_checker 会读到 -1.0 → BLOCK_NEW_RISK，
    L3 的 9 个 risk_check 字段立刻漂移（今晚的实际现象）。
    """
    dual = _load("dual_run_diff")
    import lib.data.data_layer as data_layer
    import lib.strategy.strategies as strategies

    monkeypatch.setattr(data_layer, "get_market_sentiment", lambda *a, **k: -1.0)
    monkeypatch.setattr(strategies, "get_market_sentiment", lambda *a, **k: -1.0)

    with dual.pinned_environment():
        with dual._inject_shared_data(["600519", "000001", "000858"]):
            result = dual.normalize(dual.capture_scan())

    gate = result["risk_check"]["macro_gate"]
    assert gate["state"] == "ALLOW_FULL"
    assert gate["regime"] == "NEUTRAL"
    assert gate["sentiment_mult"] == 1.0
    assert result["risk_check"]["risk_budget"]["equity"] > 0


def test_l3_pin_covers_all_three_binding_sites():
    """G2 机器判据：定义方 + 两个模块级 from-import 使用方都必须登记。"""
    source = (ROOT / "scripts" / "dual_run_diff.py").read_text(encoding="utf-8")
    for module in ("data_layer", "strategies", "pipeline"):
        assert f'({module}, "get_market_sentiment"' in source, (
            f"缺少 {module}.get_market_sentiment 的 pin；延迟 import 走定义方、"
            "模块级 from-import 走使用方，漏一处就会读生产状态"
        )


# ------------------------------------------------------------------ L10

def test_l10_detects_registry_drift_vs_source_scan(tmp_path, monkeypatch):
    """登记清单少一条路径，必须判红（证明不是「自己校验自己」）。"""
    probe = _load("concurrency_probe")
    original = json.loads(probe.FRONTEND_FILE.read_text(encoding="utf-8"))
    tampered = dict(original)
    tampered["normalized_paths"] = original["normalized_paths"][:-1]
    monkeypatch.setattr(probe, "FRONTEND_FILE", _fake_frontend_file(tmp_path, tampered))

    failures, _ = probe._frontend_freeze()
    assert any("drift vs source scan" in f for f in failures), failures


def test_l10_detects_count_tampering(tmp_path, monkeypatch):
    """只把 counts 改大、不动成员，同样必须判红。"""
    probe = _load("concurrency_probe")
    original = json.loads(probe.FRONTEND_FILE.read_text(encoding="utf-8"))
    tampered = dict(original)
    tampered["counts"] = dict(original["counts"])
    tampered["counts"]["normalized_paths"] = 999
    monkeypatch.setattr(probe, "FRONTEND_FILE", _fake_frontend_file(tmp_path, tampered))

    failures, _ = probe._frontend_freeze()
    assert any("counts.normalized_paths" in f for f in failures), failures


def test_l10_no_hardcoded_path_count():
    """数量只能来自源码重算，禁止再把数字抄进探针。"""
    source = (ROOT / "scripts" / "concurrency_probe.py").read_text(encoding="utf-8")
    assert "!= 36" not in source
    assert "expected 36" not in source
    assert "capture_frontend_paths" in source


# ------------------------------------------------------------------ L4 / G1

def test_l4_simple_mode_forwards_start_date(monkeypatch):
    """B2：BacktestRequest.start_date 必须真正到达引擎，否则窗口随日历漂移。"""
    from backtest.unified import BacktestRequest, BacktestRunner
    import backtest.engine as engine

    captured = {}

    def fake_run_backtest(**kwargs):
        captured.update(kwargs)
        return {"metrics": {}, "trades": []}

    monkeypatch.setattr(engine, "run_backtest", fake_run_backtest)
    BacktestRunner().run(BacktestRequest(
        mode="simple", symbols=["600519"], start_date="2025-12-22"))
    assert captured.get("start_date") == "2025-12-22"


def test_l4_full_mode_forwards_start_date(monkeypatch):
    """full 经 run_full_backtest 的 **kwargs 透传，同样必须带上窗口锚点。"""
    from backtest.unified import BacktestRequest, BacktestRunner
    import backtest.full as full

    captured = {}

    def fake_full(**kwargs):
        captured.update(kwargs)
        return {"metrics": {}, "trades": []}

    monkeypatch.setattr(full, "run_full_backtest", fake_full)
    BacktestRunner().run(BacktestRequest(
        mode="full", symbols=["600519"], start_date="2025-12-22"))
    assert captured.get("start_date") == "2025-12-22"


def test_l4_baseline_chain_pins_the_window():
    """G1：取证链路必须显式传常量锚定的 start_date，不能依赖 datetime.now()。"""
    source = (ROOT / "scripts" / "backtest_baseline.py").read_text(encoding="utf-8")
    assert "start_date=PIN_START_DATE" in source
    assert 'BASELINE_ANCHOR_DATE = "2026-11-07"' in source


# ------------------------------------------------- B1: L8 视野扩展 + Tier B 语义


def _contract(name: str) -> dict:
    return json.loads((ROOT / "docs" / "refactor" / "contracts" / name)
                      .read_text(encoding="utf-8"))


def test_b1_scope_covers_websocket_and_middlewares():
    """上午报告 §5 的盲区：/ws 与两个中间件此前一个调用都没被统计。"""
    b = _load("blocking_io_check")
    d = b.capture()
    keys = {e["key"] for e in d["extended_endpoints"]}
    assert "WEBSOCKET /ws" in keys
    assert {"MIDDLEWARE track_performance", "MIDDLEWARE add_security_headers"} <= keys
    ws = next(e for e in d["extended_endpoints"] if e["key"] == "WEBSOCKET /ws")
    assert "_cache_get" in ws["blocking"], "async helper _get_ws_data 必须被展开"
    assert any("get_realtime_quotes" in c for c in ws["blocking"])


def test_b1_http_endpoints_stay_l7_aligned():
    """扩展视野不得改动既有 68 个 HTTP 端点集合（L7/L9/L10 的前提）。"""
    b = _load("blocking_io_check")
    d = b.capture()
    own = _contract("route-ownership.json")
    assert {(e["method"], e["path"]) for e in d["endpoints"]} == \
           {(e["method"], e["path"]) for e in own["endpoints"]}


def test_b1_extended_blocking_addition_is_red():
    """在 /ws 里加一个阻塞调用必须判红 —— 这正是 B1 之前做不到的事。"""
    b = _load("blocking_io_check")
    base = b.capture()
    mut = copy.deepcopy(base)
    ws = next(e for e in mut["extended_endpoints"] if e["key"] == "WEBSOCKET /ws")
    ws["blocking"]["open"] = ws["blocking"].get("open", 0) + 1
    b._recount(mut)
    failures = b.check(mut, base, b.capture_tier_b(base))
    assert any("extended blocking INCREASED" in f for f in failures), failures


def test_b1_extended_endpoint_removal_is_red():
    b = _load("blocking_io_check")
    base = b.capture()
    mut = copy.deepcopy(base)
    mut["extended_endpoints"] = mut["extended_endpoints"][:-1]
    b._recount(mut)
    failures = b.check(mut, base, b.capture_tier_b(base))
    assert any("extended endpoint set" in f for f in failures), failures


def test_b1_tier_b_semantic_rules():
    """全目录遍历 + 压缩是重 IO，不能被单名 override 稀释回 light_io。"""
    b = _load("blocking_io_check")
    peers = frozenset({"cache.rglob", "zf.write", "zipfile.ZipFile"})
    assert b._tier_b_group("cache.rglob", peers) == "compute_pending"
    assert b._tier_b_group("zf.write", peers) == "compute_pending"
    assert b._tier_b_group("cache.rglob") == "light_io"


def test_b1_tier_b_register_reclassifies_backup_create():
    """B1 把它从 light_io 翻成 compute_pending；B2 更进一步把它 offload 掉。"""
    reg = _contract("blocking-io-tier-b.json")
    assert reg["counts"]["unclassified_entries"] == 0
    assert any(r["endpoint"] == "WEBSOCKET /ws" for r in reg["registered"])
    assert not any(r["endpoint"] == "POST /api/backup/create" for r in reg["registered"]), \
        "B2 后压缩体已进线程池，端点不应再有裸阻塞符号"
    base = _contract("blocking-io-baseline.json")
    ep = next(e for e in base["endpoints"] if e["key"] == "POST /api/backup/create")
    assert any("to_thread" in c for c in ep["offloaded"]), ep["offloaded"]


# ------------------------------------------------- B2: WS / backup / rate limit


def test_b2_ws_uses_single_broadcast_loop():
    """N 个连接不再各自 sleep+构造；端点保留一次调用以维持 L8 helper 视野。"""
    api_src = (ROOT / "api.py").read_text(encoding="utf-8")
    common_src = (ROOT / "routers" / "common.py").read_text(encoding="utf-8")
    assert api_src.count("await _get_ws_data()") == 1
    assert "asyncio.sleep(10)" not in api_src
    assert "async def _ws_broadcast_loop" in common_src
    assert "def broadcast(" in common_src
    assert "create_task(_ws_broadcast_loop())" in api_src
    assert "task.cancel()" in api_src


def test_b2_ws_broadcast_reaches_all_and_drops_stale():
    from routers.common import ConnectionManager

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

    class BrokenWS:
        async def send_json(self, payload):
            raise RuntimeError("closed")

    mgr = ConnectionManager()
    good, bad = FakeWS(), BrokenWS()
    mgr.active_connections = [good, bad]
    asyncio.run(mgr.broadcast({"x": 1}))
    assert good.sent == [{"x": 1}]
    assert bad not in mgr.active_connections, "推送失败的连接必须就地摘除"


def test_b2_backup_whitelist_excludes_cache(tmp_path, monkeypatch):
    """根因修法：不打包 cache/，压缩体进线程池，异常不留半截 zip。"""
    src = (ROOT / "routers" / "backup.py").read_text(encoding="utf-8")
    body = src[src.index("def _compute_backup_create"):
               src.index('@router.post("/api/backup/create"')]
    assert '"cache/"' not in body
    assert "asyncio.to_thread(_compute_backup_create)" in src
    assert "tmp.replace(backup_file)" in body

    monkeypatch.chdir(tmp_path)
    (tmp_path / "snapshots").mkdir(exist_ok=True)
    (tmp_path / "snapshots" / "s.json").write_text("{}", encoding="utf-8")
    (tmp_path / "cache").mkdir(exist_ok=True)
    (tmp_path / "cache" / "secret.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    from routers.backup import _compute_backup_create
    out = _compute_backup_create()
    import zipfile
    with zipfile.ZipFile(out["file"]) as zf:
        names = zf.namelist()
    assert any("snapshots" in n for n in names)
    assert not any("cache" in n for n in names), names


def test_b2_rate_limiter_single_source_and_bucketed(monkeypatch):
    """两套进程级实例合并为一个；不同数据源分桶互不排队。"""
    from lib.data import cache, data_layer
    assert data_layer.rate_limiter is cache.rate_limiter
    assert not hasattr(data_layer, "_RateLimiter"), "本地第二套限流器必须删除"
    assert data_layer.RATE_LIMIT_CALLS_PER_SEC == cache.CACHE_RATE_LIMIT

    rl = cache.RateLimiter(max_calls_per_second=1000)
    calls = []
    monkeypatch.setattr(cache.time, "sleep", lambda s: calls.append(s))
    rl.acquire("source_a")
    rl.acquire("source_b")
    assert calls == [], "不同 bucket 不应互相排队"
    rl.acquire("source_b")
    assert len(calls) == 1, "同一 bucket 内仍必须限速"


# ------------------------------------------------- B3: P2 性能债（低风险三项）


def _perf_mod():
    from lib.infra import perf_metrics
    return perf_metrics


def test_b3_perf_flush_no_loss_no_duplicate_under_concurrency(tmp_path):
    """文件 IO 移出缓冲锁后，「不丢不重」仍是硬约束。"""
    from concurrent.futures import ThreadPoolExecutor
    pm = _perf_mod()
    pm.clear_metrics()
    path = tmp_path / "perf_baseline.json"
    total = 120  # 会多次触发 FLUSH_THRESHOLD=50 的自动 flush

    def worker(i):
        pm.record_metric({"i": i}, path)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(worker, range(total)))
    pm.flush_metrics(path)
    ids = [d["i"] for d in json.loads(path.read_text(encoding="utf-8"))]
    assert len(ids) == len(set(ids)) == total


def test_b3_perf_failed_flush_restores_buffer():
    """IO 失败必须把快照还回缓冲头部，且保持顺序（否则丢指标）。"""
    pm = _perf_mod()
    pm.clear_metrics()
    pm._perf_metrics.extend([{"i": 0}, {"i": 1}, {"i": 2}])

    class BadPath:
        def exists(self):
            return False

        @property
        def parent(self):
            return self

        def mkdir(self, **kwargs):
            pass

        def read_text(self, **kwargs):
            return "[]"

        def write_text(self, *args, **kwargs):
            raise OSError("disk full")

    assert pm.flush_metrics(BadPath()) == 0
    assert [d["i"] for d in pm._perf_metrics] == [0, 1, 2]
    pm.clear_metrics()


def test_b3_cache_locks_are_sharded_and_stable(monkeypatch, tmp_path):
    """不同 key 用不同锁；同一 key 始终同一把锁。"""
    from lib.infra import api_core
    assert api_core._CACHE_SHARDS > 1
    assert len({id(api_core._lock_for(f"k{i}")) for i in range(64)}) > 1
    assert api_core._lock_for("same") is api_core._lock_for("same")

    # 并发读写不同 key 不丢数据（缓存目录重定向，绝不写生产 cache/）
    monkeypatch.setattr(api_core, "CACHE_DIR", tmp_path)
    from concurrent.futures import ThreadPoolExecutor

    def roundtrip(i):
        api_core._cache_set(f"k{i}", {"v": i})
        return api_core._cache_get(f"k{i}", max_age=60)

    with ThreadPoolExecutor(max_workers=8) as ex:
        out = list(ex.map(roundtrip, range(32)))
    assert [d["v"] for d in out] == list(range(32))


def test_b3_ml_predictor_singleton_reuses_and_invalidates(monkeypatch):
    """单例复用同一实例；模型目录指纹变化时必须重建。"""
    import lib.ml.ml_predictor as mp
    monkeypatch.setattr(mp, "_predictor_singleton", None)
    monkeypatch.setattr(mp, "_predictor_signature", ())
    created = []

    class FakePredictor:
        def __init__(self):
            created.append(self)

    monkeypatch.setattr(mp, "MLPredictor", FakePredictor)
    monkeypatch.setattr(mp, "_model_signature", lambda: ("v1",))
    a, b = mp.get_predictor(), mp.get_predictor()
    assert a is b and len(created) == 1
    monkeypatch.setattr(mp, "_model_signature", lambda: ("v2",))
    c = mp.get_predictor()
    assert c is not a and len(created) == 2


# ------------------------------------------------- B4: 扫描单飞 + 板块复用


def test_b4_inflight_registry_is_identity_scoped():
    """旧完成回调不能误清新登记的任务。"""
    from lib.infra import scan_task_registry as r
    r.clear_inflight_scans()
    first, other = object(), object()
    r.set_inflight_scan("dashboard:balanced", first)
    assert r.get_inflight_scan("dashboard:balanced") is first
    r.clear_inflight_scan("dashboard:balanced", other)
    assert r.get_inflight_scan("dashboard:balanced") is first
    r.clear_inflight_scan("dashboard:balanced", first)
    assert r.get_inflight_scan("dashboard:balanced") is None


def test_b4_dashboard_scan_single_flight(monkeypatch):
    """两个并发 dashboard 请求只起一次不可取消的重扫描。"""
    import routers.system as sysmod
    from lib.infra import scan_task_registry as r
    r.clear_inflight_scans()
    calls = []

    class Orchestrator:
        def run_all_strategies(self):
            calls.append(1)
            threading.Event().wait(0.2)  # 不用 time.sleep（conftest 会 patch 成 no-op）
            return {"results": {}}

    monkeypatch.setattr(sysmod, "_cache_set", lambda *a, **k: None)

    async def two_requests():
        return await asyncio.gather(
            sysmod._ensure_dashboard_scan("balanced", Orchestrator()),
            sysmod._ensure_dashboard_scan("balanced", Orchestrator()),
        )

    asyncio.run(two_requests())
    assert len(calls) == 1, f"单飞失效：起了 {len(calls)} 次重扫描"
    assert r.get_inflight_scan("dashboard:balanced") is None


def test_b4_hot_board_reuses_raw_copy_not_filtered_result():
    """复用必须发生在 symbols 过滤 / 权重调整之前，且保留降频回退路径。"""
    src = (ROOT / "lib" / "strategy" / "pipeline.py").read_text(encoding="utf-8")
    assert "raw_hot_boards = df.copy()" in src
    assert src.index("raw_hot_boards = df.copy()") < src.index("if symbols:"), (
        "原始副本必须在 symbols 过滤之前取，否则主线板块过滤口径会变"
    )
    assert "hot_boards = raw_hot_boards" in src
    assert "hot_boards = get_hot_board_candidates(config)" in src, \
        "策略被降频/失败时必须回退到原路径"
    assert "sectors = {str(row.get('板块', '')).strip()" in src, \
        "同一板块名应去重，避免重复建连"
