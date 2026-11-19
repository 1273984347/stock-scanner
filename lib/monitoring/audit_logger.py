"""审计日志 —— 记录每次扫描的决策过程，支持事后分析

每次扫描记录：
- 扫描时间、预设、市场状态
- 各策略候选股及评分
- 风控检查结果
- 组合优化结果
- 最终决策（买入/卖出/持有）
"""
import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "cache" / "audit.db"

# P1-3（2026-11-20）：审计保留期——此前只增不减（每扫描 ≤50KB raw_json），
# 决策审计在保留期内可回查（/api/audit），过期清理。
AUDIT_RETENTION_DAYS = 90

# §4.2 P1 #11 修复 (2026-06-08, lib-incremental-audit §2.5):
# 旧实现 log_scan 等模块函数每次新建 conn (sqlite3.connect), 多线程并发
# 调用可能导致 "database is locked" 错误 / 写入截断
# 修复: 模块级 Lock (进程级单例), log_scan / get_recent_scans 等关键写入持锁
_LOG_LOCK = threading.Lock()


def _get_db():
    """获取数据库连接（评审 P0-1 就绪链 2026-11-20：WAL + busy_timeout——
    并发写/读不互堵的前提；跨进程多连接兜底）"""
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.Error:
        pass  # WAL 不可用环境（如网络盘）退回默认日志模式，锁仍在
    _init_db(conn)
    return conn


def _init_db(conn):
    """初始化表结构"""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS scan_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_time TEXT NOT NULL,
        preset TEXT,
        sentiment REAL,
        macro_state TEXT,
        risk_recommendation TEXT,
        total_candidates INTEGER,
        portfolio_n_positions INTEGER,
        portfolio_expected_return REAL,
        portfolio_sharpe REAL,
        factor_health TEXT,
        raw_json TEXT
    );

    CREATE TABLE IF NOT EXISTS stock_decisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER,
        code TEXT NOT NULL,
        name TEXT,
        strategy TEXT,
        score REAL,
        rating TEXT,
        sector TEXT,
        weight REAL,
        action TEXT,
        reason TEXT,
        FOREIGN KEY (scan_id) REFERENCES scan_logs(id)
    );

    CREATE INDEX IF NOT EXISTS idx_scan_time ON scan_logs(scan_time);
    CREATE INDEX IF NOT EXISTS idx_stock_code ON stock_decisions(code);
    """)


def log_scan(scan_result: Dict) -> int:
    """记录一次完整扫描（写入全链持进程级 _LOG_LOCK）

    架构评审 P0-1 就绪链（2026-11-20）：``_LOG_LOCK`` 此前定义后**从未 acquire**
    （:23-25 注释与代码不符，「写了没接线」同族，见坑表 P-108/P-109）——offload
    默认值翻转 all 后，线程池并发写入会撞 ``database is locked`` → 审计记录静默丢失。

    Args:
        scan_result: run_all_strategies() 的返回值

    Returns:
        scan_id（0 = 写入失败）
    """
    with _LOG_LOCK:
        return _log_scan_locked(scan_result)


def _prune_old_scans(conn) -> int:
    """按保留期清理过期审计（评审 P1-3）：scan_logs + 关联 stock_decisions 同清。

    scan_time 存 ISO 字符串，同格式下字典序 == 时间序。只在 log_scan 写入路径
    触发（WAL 下 DELETE 成本可忽略）；失败不阻塞本次写入。
    """
    try:
        cutoff = (datetime.now() - timedelta(days=AUDIT_RETENTION_DAYS)).isoformat()
        old = conn.execute(
            "SELECT id FROM scan_logs WHERE scan_time < ?", (cutoff,)).fetchall()
        if not old:
            return 0
        for row in old:
            conn.execute("DELETE FROM stock_decisions WHERE scan_id = ?", (row["id"],))
        conn.execute("DELETE FROM scan_logs WHERE scan_time < ?", (cutoff,))
        conn.commit()
        logger.info("audit retention: 清理 %d 条超过 %d 天的审计记录",
                    len(old), AUDIT_RETENTION_DAYS)
        return len(old)
    except Exception as e:  # noqa: BLE001 —— 卫生措施失败不阻塞审计写入
        logger.debug(f"audit retention 清理失败（不阻塞）: {e}")
        return 0


def _log_scan_locked(scan_result: Dict) -> int:
    """log_scan 的写入体（调用方须已持 _LOG_LOCK）。"""
    conn = _get_db()
    try:
        risk = scan_result.get("risk_check", {})
        portfolio = scan_result.get("portfolio", {})
        health = scan_result.get("factor_health", {})

        # 统计候选股数
        total = sum(len(df) if hasattr(df, '__len__') else 0
                    for df in scan_result.get("results", {}).values())

        cursor = conn.execute("""
        INSERT INTO scan_logs (scan_time, preset, sentiment, macro_state,
                              risk_recommendation, total_candidates,
                              portfolio_n_positions, portfolio_expected_return,
                              portfolio_sharpe, factor_health, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            scan_result.get("preset", ""),
            risk.get("sentiment", 0),
            risk.get("macro_gate", {}).get("state", ""),
            risk.get("recommendation", ""),
            total,
            portfolio.get("n_positions", 0),
            portfolio.get("expected_return", 0),
            portfolio.get("sharpe_ratio", 0),
            health.get("recommendation", ""),
            json.dumps(scan_result, default=str, ensure_ascii=False)[:50000],
        ))
        scan_id = cursor.lastrowid

        # 记录每只股票的决策
        results = scan_result.get("results", {})
        for strategy_name, df in results.items():
            if df is None or not hasattr(df, '__iter__'):
                continue
            try:
                for _, row in df.iterrows() if hasattr(df, 'iterrows') else []:
                    code = str(row.get("代码", ""))
                    if not code:
                        continue
                    score = row.get("综合评分")
                    conn.execute("""
                    INSERT INTO stock_decisions (scan_id, code, name, strategy,
                                                score, rating, sector, weight, action, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        scan_id, code,
                        str(row.get("名称", "")),
                        strategy_name,
                        score if score is not None else row.get("置信度"),
                        row.get("评级", ""),
                        row.get("行业", ""),
                        portfolio.get("weights", {}).get(code, 0),
                        "SELECTED" if score is not None else "SCREENED",
                        f"策略{strategy_name}选出",
                    ))
            except Exception as e:
                logger.debug(f"[audit_logger] 股票决策记录写入异常 [{code}]: {e}")
                continue

        conn.commit()
        # P1-3：retention 卫生措施（失败不阻塞，见 _prune_old_scans）
        _prune_old_scans(conn)
        return scan_id
    except Exception as e:
        logger.error(f"审计日志写入失败: {e}")
        return 0
    finally:
        conn.close()


def get_scan_history(limit: int = 20) -> list:
    """获取扫描历史"""
    conn = _get_db()
    try:
        rows = conn.execute("""
        SELECT id, scan_time, preset, sentiment, macro_state,
               risk_recommendation, total_candidates,
               portfolio_n_positions, portfolio_sharpe, factor_health
        FROM scan_logs ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_scan_detail(scan_id: int) -> Optional[Dict]:
    """获取某次扫描的详情"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM scan_logs WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            return None
        result = dict(row)

        # 关联的股票决策
        stocks = conn.execute("""
        SELECT code, name, strategy, score, rating, sector, weight, action
        FROM stock_decisions WHERE scan_id = ?
        """, (scan_id,)).fetchall()
        result["stocks"] = [dict(s) for s in stocks]
        return result
    finally:
        conn.close()


def get_stock_history(code: str, limit: int = 20) -> list:
    """获取某只股票的历史决策记录"""
    conn = _get_db()
    try:
        rows = conn.execute("""
        SELECT sd.*, sl.scan_time, sl.sentiment
        FROM stock_decisions sd
        JOIN scan_logs sl ON sd.scan_id = sl.id
        WHERE sd.code = ?
        ORDER BY sl.id DESC LIMIT ?
        """, (code, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
