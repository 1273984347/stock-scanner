"""IC 报告生成

build_ic_report_text(date, ic_stats, weight_changes) -> str:
    生成 IC 健康报告文字内容

build_ic_report(date) -> str:
    读取 ic_history 和 factor_weights，生成完整报告
    返回文字报告字符串

save_ic_report(report: str, date: str) -> str:
    保存至 cache/ic/ic_report_{date}.json
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

IC_REPORT_DIR = Path("cache/ic")

def build_ic_report_text(date: str, ic_stats: Dict, weight_changes: Dict,
                         mode: str = "dynamic", fallback_reason: str = None) -> str:
    """生成 IC 健康报告文字内容"""
    lines = [
        f"IC 因子健康报告 ({date})",
        "=" * 32,
        f"模式: {'动态权重' if mode == 'dynamic' else '固定权重'}",
    ]

    if fallback_reason:
        lines.append(f"降级原因: {fallback_reason}")

    lines.append("")
    lines.append("■ 因子 IC/IR 排名")

    # 按 IC 均值排序
    sorted_factors = sorted(
        ic_stats.items(),
        key=lambda x: x[1].get("ic_mean", 0),
        reverse=True
    )

    for i, (factor, data) in enumerate(sorted_factors, 1):
        ic = data.get("ic_mean", 0)
        ir = data.get("ir", 0)
        wr = data.get("win_rate", 0)
        n = data.get("n_valid_days", 0)
        ch = weight_changes.get(factor, {}).get("change", "unchanged")
        arrow = {"up": "↑", "down": "↓", "emergency": "⚠️", "unchanged": "→"}[ch]

        lines.append(f"  {i}. {factor:<16} IC={ic:+.4f}  IR={ir:+.2f}  "
                     f"胜率={wr:.0%}  {n}天  {arrow}")

    lines.append("")
    lines.append("■ 权重变化")

    changed = {k: v for k, v in weight_changes.items() if v.get("change") != "unchanged"}
    if changed:
        for factor, info in changed.items():
            old = info.get("old_weight", 0)
            new = info.get("new_weight", 0)
            reason = info.get("reason", "")
            lines.append(f"  {factor}: {old:.3f} → {new:.3f}  {reason}")
    else:
        lines.append("  本期无权重变化")

    lines.append("")
    lines.append("仅供参考，不构成投资建议。")

    return "\n".join(lines)

def build_ic_report(date: str = None) -> str:
    """构建完整 IC 报告"""
    from lib.ic.ic_calculator import load_ic_history
    from lib.ic.weight_adjuster import load_weights

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    ic_history = load_ic_history()
    weights_data = load_weights() or {}

    ic_stats = {
        factor: data["stats"]
        for factor, data in ic_history.get("factors", {}).items()
    }

    weight_changes = {}
    for factor in ic_stats.keys():
        w = weights_data.get("weights", {})
        preset_w = _get_preset_weight(factor)
        old = preset_w
        new = w.get(factor, preset_w)
        ch = weights_data.get("changes", {}).get(factor, "unchanged")
        reason = weights_data.get("reasons", {}).get(factor, "")
        weight_changes[factor] = {
            "old_weight": old,
            "new_weight": new,
            "change": ch,
            "reason": reason,
        }

    report = build_ic_report_text(
        date, ic_stats, weight_changes,
        mode=weights_data.get("mode", "fixed"),
        fallback_reason=weights_data.get("fallback_reason"),
    )

    save_ic_report(report, date)
    return report

def _get_preset_weight(factor: str) -> float:
    """从 FactorConfig 获取 preset 权重"""
    try:
        from config import get_config
        cfg = get_config("balanced")
        return cfg.factor.factor_weights.get(factor, 0.05)
    except Exception:
        return 0.05

def save_ic_report(report: str, date: str) -> str:
    """保存 IC 报告"""
    IC_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = IC_REPORT_DIR / f"ic_report_{date}.json"
    payload = {"date": date, "report": report}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(f"[ic_reporter] IC 报告已保存: {path}")
    return str(path)
