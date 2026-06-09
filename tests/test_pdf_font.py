"""P2-2: PDF 中文字体跨平台路径检测测试

覆盖：
1. 各平台候选路径返回正确
2. 找不到字体时 resolve_chinese_font_path() 返回 None
3. PDFReportGenerator 找不到字体时显式抛 RuntimeError（不静默 fallback）
4. 找到字体时正确注册
5. 环境变量 STOCK_FONT_PATH 覆盖优先级
"""

import os
import sys
import platform
from unittest.mock import patch, MagicMock

import pytest


# 必须先 mock 掉 reportlab 内部 import 副作用（ttf 注册需要真实文件）
# 我们的测试焦点是路径解析，TTFont.registerFont 用 mock 替代
@pytest.fixture
def mock_reportlab_font():
    """Mock reportlab.pdfbase.ttfonts.TTFont + pdfmetrics.registerFont"""
    # CI 无 reportlab 时 lib.pdf_report.TTFont/pdfmetrics 属性不存在 (try/except import),
    # patch 默认 AttributeError。create=True 让 patch 在属性缺失时创建 mock 属性。
    with patch("lib.pdf_report.HAS_REPORTLAB", True), \
         patch("lib.pdf_report.TTFont", create=True) as mock_ttfont, \
         patch("lib.pdf_report.pdfmetrics", create=True) as mock_metrics:
        mock_ttfont.return_value = MagicMock()
        yield mock_ttfont, mock_metrics


# ---------------------------------------------------------------------------
# get_chinese_font_candidates
# ---------------------------------------------------------------------------

class TestGetChineseFontCandidates:
    def test_windows_returns_msyh_first(self):
        with patch.object(platform, "system", return_value="Windows"):
            cands = _call_candidates()
        assert cands[0].lower().endswith("msyh.ttc")
        # Windows 候选至少包含 微软雅黑 / 黑体 / 宋体
        joined = "|".join(cands).lower()
        assert "msyh" in joined
        assert "simhei" in joined or "simsun" in joined

    def test_macos_returns_pingfang_first(self):
        with patch.object(platform, "system", return_value="Darwin"):
            cands = _call_candidates()
        assert cands[0].endswith("PingFang.ttc")
        assert any("STHeiti" in p for p in cands)

    def test_linux_returns_wqy_or_noto(self):
        with patch.object(platform, "system", return_value="Linux"):
            cands = _call_candidates()
        # 第一个候选应是 wqy 或 noto
        first = cands[0]
        assert ("wqy" in first) or ("Noto" in first), f"unexpected first: {first}"

    def test_unknown_platform_falls_back_to_linux_style(self):
        with patch.object(platform, "system", return_value="FreeBSD"):
            cands = _call_candidates()
        # 未知平台不应抛错，应返回兜底列表
        assert isinstance(cands, list)
        assert len(cands) > 0

    def test_env_override_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("STOCK_FONT_PATH", "/custom/path/myfont.ttc")
        cands = _call_candidates()
        assert cands[0] == "/custom/path/myfont.ttc"


# ---------------------------------------------------------------------------
# resolve_chinese_font_path
# ---------------------------------------------------------------------------

class TestResolveChineseFontPath:
    def test_returns_first_existing_path(self):
        fake = ["/nonexistent/a.ttf", "/fake/b.ttf", "/fake/c.ttc"]
        with patch("lib.pdf_report.get_chinese_font_candidates", return_value=fake), \
             patch("os.path.isfile", side_effect=lambda p: p == "/fake/b.ttf"):
            assert resolve_chinese_font_path() == "/fake/b.ttf"

    def test_returns_none_when_none_exist(self):
        fake = ["/nope/a.ttf", "/nope/b.ttf"]
        with patch("lib.pdf_report.get_chinese_font_candidates", return_value=fake), \
             patch("os.path.isfile", return_value=False):
            assert resolve_chinese_font_path() is None

    def test_skips_oserror(self):
        def boom(_):
            raise OSError("permission denied")
        with patch("lib.pdf_report.get_chinese_font_candidates",
                   return_value=["/nope/a.ttf", "/ok/b.ttf"]), \
             patch("os.path.isfile", side_effect=boom):
            # OSError 路径被跳过，最终 None
            assert resolve_chinese_font_path() is None

    def test_empty_candidate_list(self):
        with patch("lib.pdf_report.get_chinese_font_candidates", return_value=[]):
            assert resolve_chinese_font_path() is None


# ---------------------------------------------------------------------------
# PDFReportGenerator 集成
# ---------------------------------------------------------------------------

class TestPDFReportGeneratorFontSetup:
    def test_raises_runtime_error_when_no_font(
        self, mock_reportlab_font, monkeypatch
    ):
        """P2-2 关键：找不到字体时显式抛错，不静默用 Helvetica"""
        monkeypatch.setenv("STOCK_FONT_PATH", "")  # 清空覆盖
        # 所有平台路径都不存在
        with patch("lib.pdf_report.resolve_chinese_font_path", return_value=None), \
             patch("lib.pdf_report.get_chinese_font_candidates",
                   return_value=["/nope/msyh.ttc", "/nope/PingFang.ttc"]):
            with pytest.raises(RuntimeError) as exc_info:
                from lib.pdf_report import PDFReportGenerator
                PDFReportGenerator()

        msg = str(exc_info.value)
        # 错误信息应包含：平台、候选路径、修复建议
        assert "No Chinese font found" in msg
        assert "msyh.ttc" in msg or "PingFang.ttc" in msg
        assert "STOCK_FONT_PATH" in msg  # 给出环境变量修复建议

    def test_registers_font_when_found(self, mock_reportlab_font):
        """正常路径：找到字体 → 注册成功 → _chinese_font = 'Chinese'"""
        from lib.pdf_report import PDFReportGenerator
        with patch("lib.pdf_report.resolve_chinese_font_path",
                   return_value="/fake/font.ttc"):
            gen = PDFReportGenerator()
        assert gen._chinese_font == "Chinese"
        assert gen._chinese_font_path == "/fake/font.ttc"
        # 确认 pdfmetrics.registerFont 被调用
        mock_ttfont, mock_metrics = mock_reportlab_font
        mock_metrics.registerFont.assert_called_once()

    def test_raises_when_register_fails(
        self, mock_reportlab_font, monkeypatch
    ):
        """TTFont.register 内部抛错 → 包成 RuntimeError 带文件路径"""
        from lib.pdf_report import PDFReportGenerator
        monkeypatch.setenv("STOCK_FONT_PATH", "")
        with patch("lib.pdf_report.resolve_chinese_font_path",
                   return_value="/corrupt/font.ttc"), \
             patch("lib.pdf_report.pdfmetrics.registerFont",
                   side_effect=OSError("bad ttf")):
            with pytest.raises(RuntimeError) as exc_info:
                PDFReportGenerator()
        assert "/corrupt/font.ttc" in str(exc_info.value)
        assert "Failed to register" in str(exc_info.value)

    def test_skips_setup_when_reportlab_missing(self, monkeypatch):
        """HAS_REPORTLAB=False 时不抛错（向后兼容 reportlab 未装的场景）"""
        with patch("lib.pdf_report.HAS_REPORTLAB", False):
            from lib.pdf_report import PDFReportGenerator
            gen = PDFReportGenerator()
        # 不应抛错，_chinese_font 应保持为 init 默认（无）
        assert not hasattr(gen, "_chinese_font") or gen._chinese_font is None \
            or gen._chinese_font == "Helvetica"  # 视实现而定


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _call_candidates():
    """调用模块级函数，避开 reportlab 是否真的 import 成功的影响"""
    from lib.pdf_report import get_chinese_font_candidates
    return get_chinese_font_candidates()


def resolve_chinese_font_path():
    from lib.pdf_report import resolve_chinese_font_path
    return resolve_chinese_font_path()
