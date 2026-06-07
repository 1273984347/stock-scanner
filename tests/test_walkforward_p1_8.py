"""P1-8: lib/walkforward.py save_walkforward_results 避免覆盖

修复前: 文件名 hash 仅基于 strategy_name+date, 必覆盖
修复后: 引入 rounds/summary 内容 hash + 毫秒级时间戳
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import time

from lib import walkforward


def test_p1_8_save_results_no_overwrite_same_strategy_same_day(tmp_path, monkeypatch):
    """P1-8: 同日同 strategy 多次 save 不应覆盖前一次"""
    # 重定向 cache_dir 到 tmp_path
    fake_file = tmp_path / "fake_walkforward.py"
    fake_file.write_text("")
    monkeypatch.setattr(walkforward, "__file__", str(fake_file))

    summary1 = {"rounds": 3, "median_return": 0.05, "median_sharpe": 1.0, "total_trades": 30}
    summary2 = {"rounds": 5, "median_return": 0.08, "median_sharpe": 1.5, "total_trades": 50}

    path1 = walkforward.save_walkforward_results("test_strat", summary1, rounds_count=3)
    time.sleep(0.01)
    path2 = walkforward.save_walkforward_results("test_strat", summary2, rounds_count=5)

    assert path1 != path2, (
        f"P1-8 修复未完成: 同日同 strategy 多次 save 返回相同路径 {path1}，必覆盖。"
    )
    assert path1.exists(), f"path1 应存在: {path1}"
    assert path2.exists(), f"path2 应存在: {path2}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
