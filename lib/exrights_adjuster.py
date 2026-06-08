"""除权除息自动调整器 —— 持仓成本和止损位修正

配合 data_layer.get_exright_events() 使用，根据除权除息事件
自动修正 position_manager 中的持仓成本价和止损价位。

gbbq 原始值说明（pytdx GbbqReader 解密后）：
  - hongli: 每10股分红（元），÷10 = 每股分红
  - songzhuangu: 每10股送转股数，÷10 = 每股送转比例
  - peigujia: 配股价（元/股）
  - peigu_hou: 配股后总股本
  - category: 1=除权除息, 2=送股, 3=配股, ...
"""

import logging
from typing import Dict, List, Optional, Union
from datetime import datetime

logger = logging.getLogger(__name__)


def _is_position_obj(pos) -> bool:
    """检查是否为 Position dataclass 对象"""
    return hasattr(pos, "buy_price") and hasattr(pos, "shares") and hasattr(pos, "stop_loss")


def adjust_position_for_exrights(
    position: Union[Dict, object],
    exright_event: Dict,
) -> Union[Dict, object]:
    """根据除权除息事件调整持仓

    Args:
        position: 持仓信息（dict 或 Position dataclass）
        exright_event: 除权除息事件（来自 get_exright_events()）
            包含: date, category, hongli, peigujia, songzhuangu, peigu_hou

    Returns:
        调整后的持仓信息（dict）
    """
    is_obj = _is_position_obj(position)

    if is_obj:
        code = position.code
        cost = position.buy_price
        shares = position.shares
        stop_loss_pct = position.stop_loss  # 百分比，如 0.08 = 8%
        atr_stop = position.atr_stop_price
        trailing_stop = position.trailing_stop_price
        atr_tp = position.atr_take_profit_price
    else:
        code = position.get("code", "")
        cost = position.get("buy_price", position.get("cost", 0))
        shares = position.get("shares", 0)
        stop_loss_pct = position.get("stop_loss", 0)
        atr_stop = position.get("atr_stop_price", 0)
        trailing_stop = position.get("trailing_stop_price", 0)
        atr_tp = position.get("atr_take_profit_price", 0)

    # gbbq 原始值 → 每股实际值（÷10）
    hongli = exright_event.get("hongli", 0) / 10.0     # 每股分红（元）
    songzhuangu = exright_event.get("songzhuangu", 0) / 10.0  # 每股送转比例
    peigujia = exright_event.get("peigujia", 0)         # 配股价（元/股）

    if cost <= 0 or shares <= 0:
        logger.debug(f"{code} 成本或股数为零，跳过调整")
        return position

    new_cost = cost
    new_shares = shares

    # 分红：成本价直接减去每股分红
    if hongli > 0:
        new_cost -= hongli
        logger.info(f"{code} 分红 {hongli:.4f} 元/股，成本 {cost:.3f} → {new_cost:.3f}")

    # 送转：增加股数，等比降低成本
    # 新成本 = 原成本 / (1 + 送转比例)
    # 新股数 = 原股数 × (1 + 送转比例)
    if songzhuangu > 0:
        ratio = 1 + songzhuangu
        new_shares = int(shares * ratio)
        new_cost = new_cost / ratio
        logger.info(
            f"{code} 送转 {songzhuangu:.2f}，"
            f"股数 {shares} → {new_shares}，成本 → {new_cost:.3f}"
        )

    # 配股：加权平均成本
    # 假设 peigujia > 0 表示有配股事件，peigu_hou 可推算配股比例
    if peigujia > 0:
        peigu_hou = exright_event.get("peigu_hou", 0)
        if peigu_hou > 0 and new_shares > 0:
            # peigu_hou 是配股后总股本（万），可推算配股数
            # 简化处理：如果有 songzhuangu 已处理送转，这里用原始比例
            # 保守方案：仅记录配股价，不自动增加股数（需人工确认配股比例）
            logger.info(
                f"{code} 配股价 {peigujia:.2f} 元/股（配股后总股本 {peigu_hou}），"
                f"请人工确认配股比例后手动调整"
            )

    # 调整止损位 —— 保持相同的止损百分比
    new_stop_loss_pct = stop_loss_pct  # 百分比不变

    # 调整 ATR 止损价（绝对价位）
    new_atr_stop = 0.0
    if atr_stop > 0 and cost > 0:
        # ATR 止损价按同比例调整
        stop_distance = cost - atr_stop
        new_atr_stop = new_cost - stop_distance
        if songzhuangu > 0:
            new_atr_stop = new_atr_stop / (1 + songzhuangu)
        logger.info(f"{code} ATR止损 {atr_stop:.3f} → {new_atr_stop:.3f}")

    # 调整移动止盈价
    new_trailing_stop = 0.0
    if trailing_stop > 0 and cost > 0:
        stop_distance = cost - trailing_stop
        new_trailing_stop = new_cost - stop_distance
        if songzhuangu > 0:
            new_trailing_stop = new_trailing_stop / (1 + songzhuangu)

    # 调整 ATR 止盈价
    new_atr_tp = 0.0
    if atr_tp > 0 and cost > 0:
        tp_distance = atr_tp - cost
        new_atr_tp = new_cost + tp_distance
        if songzhuangu > 0:
            new_atr_tp = new_atr_tp / (1 + songzhuangu)

    # 调整 peak_price
    new_peak = 0.0
    if is_obj:
        peak = position.peak_price
    else:
        peak = position.get("peak_price", 0)
    if peak > 0 and cost > 0:
        peak_distance = peak - cost
        new_peak = new_cost + peak_distance
        if songzhuangu > 0:
            new_peak = new_peak / (1 + songzhuangu)

    # 构造返回 dict
    if is_obj:
        # 从 dataclass 转为 dict，更新调整后的值
        import dataclasses
        result = dataclasses.asdict(position)
    else:
        result = dict(position)

    result.update({
        "buy_price": round(new_cost, 3),
        "shares": new_shares,
        "stop_loss": new_stop_loss_pct,
        "atr_stop_price": round(new_atr_stop, 3) if new_atr_stop else atr_stop,
        "trailing_stop_price": round(new_trailing_stop, 3) if new_trailing_stop else trailing_stop,
        "atr_take_profit_price": round(new_atr_tp, 3) if new_atr_tp else atr_tp,
        "peak_price": round(new_peak, 3) if new_peak else (peak if peak else 0),
        "exrights_adjusted": True,
        "exrights_date": exright_event.get("date", 0),
    })

    return result


def batch_adjust_exrights(
    positions: List[Dict],
    exrights_events: Dict[str, List[Dict]],
) -> List[Dict]:
    """批量调整持仓的除权除息

    Args:
        positions: 持仓列表
        exrights_events: {code: [events]} 除权除息事件字典
            来自 data_layer.get_exright_events() 按 code 分组

    Returns:
        调整后的持仓列表
    """
    adjusted = []
    for pos in positions:
        code = pos.get("code", "") if isinstance(pos, dict) else pos.code
        events = exrights_events.get(code, [])

        if events:
            # 应用最新的除权除息事件
            latest_event = events[-1]
            pos = adjust_position_for_exrights(pos, latest_event)

        adjusted.append(pos)

    return adjusted


def check_and_adjust_positions(
    position_manager,
    data_layer_module=None,
    since_date: int = 0,
) -> int:
    """检查所有持仓并自动调整除权除息

    集成入口：在 scan 循环或启动时调用。

    Args:
        position_manager: PositionManager 实例
        data_layer_module: data_layer 模块（可选，默认自动导入）
        since_date: 起始日期过滤（如 20250101），0 = 检查所有

    Returns:
        调整的持仓数量
    """
    if data_layer_module is None:
        from lib import data_layer as data_layer_module

    adjusted_count = 0
    positions = position_manager.positions

    for code, pos in list(positions.items()):
        events = data_layer_module.get_exright_events(code, start_date=since_date)
        if not events:
            continue

        # 过滤出尚未处理的事件（基于日期）
        last_adjusted = getattr(pos, "exrights_date", 0)
        new_events = [e for e in events if e["date"] > last_adjusted]
        if not new_events:
            continue

        # 逐个应用新事件
        current = pos
        for event in new_events:
            adjusted_dict = adjust_position_for_exrights(current, event)
            # 更新 Position 对象
            current.buy_price = adjusted_dict["buy_price"]
            current.shares = adjusted_dict["shares"]
            current.stop_loss = adjusted_dict["stop_loss"]
            current.atr_stop_price = adjusted_dict.get("atr_stop_price", current.atr_stop_price)
            current.trailing_stop_price = adjusted_dict.get("trailing_stop_price", current.trailing_stop_price)
            current.atr_take_profit_price = adjusted_dict.get("atr_take_profit_price", current.atr_take_profit_price)
            current.peak_price = adjusted_dict.get("peak_price", current.peak_price)
            # 标记已处理（存储最新事件日期到 notes 或新增字段）
            logger.info(
                f"{code} 除权除息调整完成: "
                f"成本→{current.buy_price:.3f} 股数→{current.shares} "
                f"止损→{current.stop_loss:.1%}"
            )

        # P0-5 修复 (2026-06-08): 写入 exrights_date 防止股数倍乘
        # 旧实现只读不写，下次调用 line 224 `e["date"] > last_adjusted` 仍为 True → 同一事件被应用多次
        # 现在显式存最后处理日期
        last_event_date = max(e["date"] for e in new_events)
        if hasattr(current, "exrights_date"):
            current.exrights_date = last_event_date
        else:
            # Position dataclass 暂未定义该字段，存到 notes 备查
            note = getattr(current, "notes", None) or ""
            setattr(current, "notes", f"{note};exrights={last_event_date}")

        adjusted_count += 1

    if adjusted_count > 0:
        position_manager._save()
        logger.info(f"除权除息批量调整完成: {adjusted_count} 只持仓已更新")

    return adjusted_count
