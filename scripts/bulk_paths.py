"""全市场数据目录路径的唯一真源（P-139）。

所有 bulk 相关脚本必须 import 本模块取路径，禁止硬编码 `OUT / "_xxx"`——
路径散落在 N 个脚本里，重构目录就要改 N 处且易漏，是"补丁加补丁"的温床。

目录结构（2026-11-25 重构后）：
    data/exports/bulk/
    ├── 01_raw_shards/      原始拉取分片（12 类，按 batch，可追溯）
    ├── 02_markers/         .done / .err 断点标记
    ├── 03_universe/        股票池代码清单
    ├── 04_repair/          逐只补拉分片 + 日志
    ├── 05_qfq/             真前复权增量（jsonl 是唯一真源）
    ├── 06_clean/           OHLC=0 清洗后数据
    ├── 07_integrated/      分片合并主表 + MANIFEST
    ├── 08_extra_market/    市场级补充（榜单/龙虎榜/日历/IPO/大盘画像）
    ├── 09_extra_depth/     板块深度（50 行业 × 5 类）
    ├── 10_extra_tick/      分时/筹码（逐只）
    ├── 11_usecases/        用途集（backtest/frontend）
    └── 12_reports/         对账/质检/清单文档
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BULK = ROOT / "data" / "exports" / "bulk"

RAW = BULK / "01_raw_shards"
MARKERS = BULK / "02_markers"
UNIVERSE = BULK / "03_universe"
REPAIR = BULK / "04_repair"
QFQ = BULK / "05_qfq"
CLEAN = BULK / "06_clean"
INTEGRATED = BULK / "07_integrated"
EXTRA_MARKET = BULK / "08_extra_market"
EXTRA_DEPTH = BULK / "09_extra_depth"
EXTRA_TICK = BULK / "10_extra_tick"
USECASES = BULK / "11_usecases"
REPORTS = BULK / "12_reports"
ARCHIVE = BULK / "00_archive"          # 早期单只试拉的原始 md（史前产物）

BACKTEST = USECASES / "backtest"
FRONTEND = USECASES / "frontend"

# 关键文件
QFQ_LOG = QFQ / "hs_qfq.jsonl"
QFQ_FAILED = QFQ / "hs_qfq_failed.jsonl"

ALL_DIRS = [RAW, MARKERS, UNIVERSE, REPAIR, QFQ, CLEAN, INTEGRATED,
            EXTRA_MARKET, EXTRA_DEPTH, EXTRA_TICK, BACKTEST, FRONTEND, REPORTS, ARCHIVE]


def ensure_dirs():
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
