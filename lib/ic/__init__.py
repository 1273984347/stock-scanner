"""IC 因子分析模块"""
from .panel_builder import build_ic_panel

__all__ = [
    "build_ic_panel",
    # TODO: 以下模块后续任务创建
    # "update_ic_history",
    # "compute_factor_ic",
    # "compute_dynamic_weights",
    # "build_ic_report",
]


def __getattr__(name):
    """Lazy import for optional modules"""
    if name == "update_ic_history" or name == "compute_factor_ic":
        from .ic_calculator import update_ic_history, compute_factor_ic
        globals()["update_ic_history"] = update_ic_history
        globals()["compute_factor_ic"] = compute_factor_ic
        return (update_ic_history, compute_factor_ic) if name == "update_ic_history" else compute_factor_ic
    if name == "compute_dynamic_weights":
        from .weight_adjuster import compute_dynamic_weights
        globals()["compute_dynamic_weights"] = compute_dynamic_weights
        return compute_dynamic_weights
    if name == "build_ic_report":
        from .ic_reporter import build_ic_report
        globals()["build_ic_report"] = build_ic_report
        return build_ic_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")