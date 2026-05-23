"""tests/reporter/test_ic_report_generator.py"""

import pytest


def test_build_ic_push_content():
    """IC 推送内容构建"""
    from reporter.ic_report_generator import build_ic_push_content

    report_text = "IC 因子健康报告 (2026-05-24)\n..."
    content = build_ic_push_content(report_text)
    assert "IC 因子健康报告" in content
    assert len(content) > 0


def test_build_emergency_content():
    """紧急告警内容构建"""
    from reporter.ic_report_generator import build_emergency_content

    content = build_emergency_content("pe_ratio", -0.1234)
    assert "IC 紧急告警" in content
    assert "pe_ratio" in content
    assert "-0.1234" in content
    assert "已自动降权" in content
