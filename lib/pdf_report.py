"""PDF 报告生成器 — 自动生成策略报告

报告内容：
1. 封面（日期、策略名称）
2. 市场概览（指数走势、板块热力图）
3. 持仓详情（成本、盈亏、持有天数）
4. 信号列表（置信度排序）
5. 回测结果（净值曲线、交易统计）
6. 风险指标（VaR、相关性矩阵）
"""

import io
from datetime import datetime
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

# 检查依赖
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm, cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False
    logger.warning("reportlab not installed, PDF generation disabled")

try:
    import matplotlib
    matplotlib.use('Agg')  # 非交互式后端
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    logger.warning("matplotlib not installed, chart generation disabled")


class PDFReportGenerator:
    """PDF 报告生成器"""

    def __init__(self):
        self._styles = None
        self._setup_fonts()

    def _setup_fonts(self):
        """设置中文字体"""
        if not HAS_REPORTLAB:
            return

        # 尝试加载中文字体
        font_paths = [
            'C:/Windows/Fonts/msyh.ttc',  # 微软雅黑
            'C:/Windows/Fonts/simsun.ttc',  # 宋体
            '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',  # Linux
        ]

        for font_path in font_paths:
            try:
                pdfmetrics.registerFont(TTFont('Chinese', font_path))
                self._chinese_font = 'Chinese'
                return
            except Exception:
                continue

        self._chinese_font = 'Helvetica'
        logger.warning("Chinese font not found, using Helvetica")

    def _get_styles(self):
        """获取样式"""
        if self._styles:
            return self._styles

        styles = getSampleStyleSheet()

        # 自定义样式
        styles.add(ParagraphStyle(
            name='ChineseTitle',
            fontName=self._chinese_font,
            fontSize=24,
            leading=30,
            alignment=1,  # 居中
            spaceAfter=20
        ))

        styles.add(ParagraphStyle(
            name='ChineseHeading',
            fontName=self._chinese_font,
            fontSize=16,
            leading=20,
            spaceBefore=12,
            spaceAfter=8
        ))

        styles.add(ParagraphStyle(
            name='ChineseBody',
            fontName=self._chinese_font,
            fontSize=10,
            leading=14,
            spaceAfter=6
        ))

        self._styles = styles
        return styles

    def generate(self, data: Dict) -> bytes:
        """生成 PDF 报告"""
        if not HAS_REPORTLAB:
            raise RuntimeError("reportlab not installed")

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []
        styles = self._get_styles()

        # 1. 封面
        story.extend(self._build_cover(data, styles))
        story.append(Spacer(1, 50))

        # 2. 市场概览
        story.extend(self._build_market_overview(data, styles))
        story.append(Spacer(1, 30))

        # 3. 持仓详情
        story.extend(self._build_positions(data, styles))
        story.append(Spacer(1, 30))

        # 4. 信号列表
        story.extend(self._build_signals(data, styles))
        story.append(Spacer(1, 30))

        # 5. 回测结果
        story.extend(self._build_backtest(data, styles))
        story.append(Spacer(1, 30))

        # 6. 风险指标
        story.extend(self._build_risk(data, styles))

        # 构建 PDF
        doc.build(story)
        return buffer.getvalue()

    def _build_cover(self, data: Dict, styles) -> list:
        """构建封面"""
        elements = []
        now = datetime.now()

        elements.append(Spacer(1, 100))
        elements.append(Paragraph("量化交易策略报告", styles['ChineseTitle']))
        elements.append(Spacer(1, 20))
        elements.append(Paragraph(f"报告日期：{now.strftime('%Y年%m月%d日')}", styles['ChineseBody']))
        elements.append(Paragraph(f"策略：{data.get('preset', '均衡策略')}", styles['ChineseBody']))
        elements.append(Paragraph(f"生成时间：{now.strftime('%H:%M:%S')}", styles['ChineseBody']))

        return elements

    def _build_market_overview(self, data: Dict, styles) -> list:
        """构建市场概览"""
        elements = []
        elements.append(Paragraph("一、市场概览", styles['ChineseHeading']))

        dashboard = data.get('dashboard', {})
        if dashboard:
            sentiment = dashboard.get('sentiment', {})
            macro = dashboard.get('macro', {})

            table_data = [
                ['指标', '数值', '状态'],
                ['市场情绪', sentiment.get('value', '--'), sentiment.get('label', '--')],
                ['宏观门控', macro.get('state', '--'), macro.get('label', '--')],
                ['建议仓位', str(dashboard.get('exposure', '--')), dashboard.get('exposure_label', '--')],
                ['组合夏普', str(dashboard.get('sharpe', '--')), '预期夏普比'],
            ]

            table = Table(table_data, colWidths=[100, 80, 120])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), self._chinese_font),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(table)
        else:
            elements.append(Paragraph("暂无市场数据", styles['ChineseBody']))

        return elements

    def _build_positions(self, data: Dict, styles) -> list:
        """构建持仓详情"""
        elements = []
        elements.append(Paragraph("二、持仓详情", styles['ChineseHeading']))

        positions = data.get('positions', [])
        if positions:
            table_data = [['代码', '名称', '行业', '成本', '现价', '盈亏', '仓位']]

            for pos in positions[:20]:  # 最多显示 20 条
                table_data.append([
                    str(pos.get('code', '--')),
                    str(pos.get('name', '--')),
                    str(pos.get('sector', '--')),
                    f"{pos.get('cost', 0):.2f}",
                    f"{pos.get('current', 0):.2f}",
                    f"{pos.get('pnl_pct', 0):.2%}",
                    f"{pos.get('weight', 0):.1%}",
                ])

            table = Table(table_data, colWidths=[60, 60, 60, 50, 50, 50, 50])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), self._chinese_font),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(table)
        else:
            elements.append(Paragraph("暂无持仓", styles['ChineseBody']))

        return elements

    def _build_signals(self, data: Dict, styles) -> list:
        """构建信号列表"""
        elements = []
        elements.append(Paragraph("三、信号列表", styles['ChineseHeading']))

        signals = data.get('signals', [])
        if signals:
            table_data = [['日期', '代码', '信号', '置信度', 'IC分数']]

            for sig in signals[:15]:  # 最多显示 15 条
                table_data.append([
                    str(sig.get('date', '--')),
                    str(sig.get('code', '--')),
                    str(sig.get('signal', '--')),
                    f"{sig.get('confidence', 0):.1%}",
                    f"{sig.get('ic_score', 0):.3f}",
                ])

            table = Table(table_data, colWidths=[70, 60, 80, 60, 60])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), self._chinese_font),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(table)

            # 信号统计
            summary = data.get('signal_summary', {})
            if summary:
                elements.append(Spacer(1, 10))
                elements.append(Paragraph(
                    f"信号统计：总数 {summary.get('total', 0)}，"
                    f"准确率 {summary.get('accuracy', 0):.1%}，"
                    f"平均5日收益 {summary.get('avg_return', 0):.2%}",
                    styles['ChineseBody']
                ))
        else:
            elements.append(Paragraph("暂无信号", styles['ChineseBody']))

        return elements

    def _build_backtest(self, data: Dict, styles) -> list:
        """构建回测结果"""
        elements = []
        elements.append(Paragraph("四、回测结果", styles['ChineseHeading']))

        backtest = data.get('backtest', {})
        if backtest:
            # 回测统计
            stats_data = [
                ['指标', '数值'],
                ['总收益', f"{backtest.get('total_return', 0):.2%}"],
                ['夏普比率', f"{backtest.get('sharpe_ratio', 0):.3f}"],
                ['最大回撤', f"{backtest.get('max_drawdown', 0):.2%}"],
                ['胜率', f"{backtest.get('win_rate', 0):.1%}"],
                ['交易次数', str(backtest.get('trades', 0))],
            ]

            table = Table(stats_data, colWidths=[100, 100])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), self._chinese_font),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(table)

            # 生成净值曲线图
            if HAS_MATPLOTLIB and backtest.get('equity_curve'):
                chart_path = self._generate_equity_chart(backtest['equity_curve'])
                if chart_path:
                    elements.append(Spacer(1, 10))
                    elements.append(Image(chart_path, width=400, height=200))
        else:
            elements.append(Paragraph("暂无回测数据", styles['ChineseBody']))

        return elements

    def _build_risk(self, data: Dict, styles) -> list:
        """构建风险指标"""
        elements = []
        elements.append(Paragraph("五、风险指标", styles['ChineseHeading']))

        risk = data.get('risk', {})
        if risk:
            risk_data = [
                ['指标', '数值', '状态'],
                ['VaR (95%)', f"{risk.get('var_95', 0):.2%}", risk.get('var_status', '--')],
                ['CVaR', f"{risk.get('cvar', 0):.2%}", risk.get('cvar_status', '--')],
                ['最大回撤', f"{risk.get('max_drawdown', 0):.2%}", risk.get('drawdown_status', '--')],
                ['波动率', f"{risk.get('volatility', 0):.2%}", risk.get('volatility_status', '--')],
            ]

            table = Table(risk_data, colWidths=[80, 80, 120])
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, -1), self._chinese_font),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(table)

            # 风险建议
            if risk.get('recommendation'):
                elements.append(Spacer(1, 10))
                elements.append(Paragraph(f"风控建议：{risk['recommendation']}", styles['ChineseBody']))
        else:
            elements.append(Paragraph("暂无风险数据", styles['ChineseBody']))

        return elements

    def _generate_equity_chart(self, equity_curve: List) -> Optional[str]:
        """生成净值曲线图"""
        if not HAS_MATPLOTLIB or not equity_curve:
            return None

        try:
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(equity_curve, color='#2962FF', linewidth=1.5)
            ax.fill_between(range(len(equity_curve)), equity_curve, alpha=0.1, color='#2962FF')
            ax.set_title('Portfolio Equity Curve', fontsize=12)
            ax.set_xlabel('Trading Days', fontsize=10)
            ax.set_ylabel('Equity', fontsize=10)
            ax.grid(True, alpha=0.3)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)

            # 保存到临时文件
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                fig.savefig(f, dpi=150, bbox_inches='tight')
                plt.close(fig)
                return f.name
        except Exception as e:
            logger.error(f"Chart generation failed: {e}")
            plt.close('all')
            return None


# 单例
pdf_generator = PDFReportGenerator()
