import { stateManager } from '/js/state.js';
import { apiClient } from '/js/api.js';
import { getThemeColor, seriesColors } from '/js/theme.js';
import { LazyChart } from '/js/utils/lazyChart.js';
import { VirtualTable } from '/js/utils/virtualTable.js';
import { exportChartJs, addExportButton, generateFilename } from '/js/utils/chartExport.js';

/**
 * PortfolioRenderer — 组合优化面板渲染器
 * 负责组合表格 + 行业饼图 + 收益波动柱状图 + 市场状态时间线
 */
class PortfolioRenderer {
  constructor() {
    this._charts = { sector: null, returnVol: null, regime: null };
    this._unsubscribers = [];
    this._regimeLoaded = false;
    this._lazy = new LazyChart('panel-portfolio');
    this._vt = null;          // VirtualTable 实例（组合表窗口化）
    this._details = [];
  }

  mount() {
    this._unsubscribers.push(
      stateManager.subscribe('scan.portfolio', (data) => this.render(data))
    );
    // 订阅实时 P&L 更新资金曲线
    this._unsubscribers.push(
      stateManager.subscribe('position_pnl', (data) => this._updateEquityCurve(data))
    );
    // 事件委托：使用 data 属性而非 inline onclick
    document.getElementById('portfolioBody')?.addEventListener('click', (e) => {
      const tr = e.target.closest('tr[data-code]');
      if (tr) {
        const code = tr.dataset.code;
        const name = tr.dataset.name || '';
        window.openStockDetail(code, name);
      }
    });
    // 首次挂载时加载 regime 时间线
    this._loadRegimeTimeline();
  }

  render(p) {
    const details = (p && p.details) ? p.details : [];
    this._renderTable(details);
    if (!details.length) return;
    this._renderStats(p);
    // 图表懒加载：只在面板可见时渲染
    this._lazy.whenVisible(
      () => {
        this._renderSectorChart(p.sector_exposure || {});
        this._renderReturnVolChart(p);
      },
      () => {
        this._charts.sector?.destroy(); this._charts.sector = null;
        this._charts.returnVol?.destroy(); this._charts.returnVol = null;
      }
    );
  }

  _renderTable(details) {
    const body = document.getElementById('portfolioBody');
    if (!body) return;
    this._details = details;
    this._ensureVt();
    this._vt.setData(details);
  }

  _ensureVt() {
    if (this._vt) return;
    const body = document.getElementById('portfolioBody');
    if (!body) return;
    this._vt = new VirtualTable(body, {
      rowHeight: 34,
      maxHeight: '50vh',
      emptyText: '暂无组合数据，点击"刷新数据"开始扫描',
      renderRow: (i, d) => this._renderPortfolioRow(i, d),
    });
  }

  _renderPortfolioRow(index, d) {
    const tr = document.createElement('tr');
    tr.dataset.code = d.code || '';
    tr.dataset.name = d.name || '';
    // 安全插入：使用 textContent 避免 XSS
    const codeTd = document.createElement('td');
    const codeLink = document.createElement('a');
    codeLink.style.cssText = 'color:var(--accent);cursor:pointer';
    codeLink.textContent = d.code || '--';
    codeTd.appendChild(codeLink);

    const nameTd = document.createElement('td');
    nameTd.textContent = d.name || '--';

    const sectorTd = document.createElement('td');
    sectorTd.textContent = d.sector || '--';

    const weightTd = document.createElement('td');
    weightTd.className = 'text-accent';
    weightTd.textContent = ((d.weight || 0) * 100).toFixed(1) + '%';

    const scoreTd = document.createElement('td');
    scoreTd.textContent = (d.score || 0).toFixed(1);

    tr.appendChild(codeTd);
    tr.appendChild(nameTd);
    tr.appendChild(sectorTd);
    tr.appendChild(weightTd);
    tr.appendChild(scoreTd);
    return tr;
  }

  _renderSectorChart(sectors) {
    const canvas = document.getElementById('sectorChart');
    const ctx = canvas?.getContext('2d');
    if (!ctx) return;
    this._charts.sector?.destroy();
    const labels = Object.keys(sectors);
    const values = labels.map(k => Number(sectors[k]) * 100);
    if (!labels.length) return;
    this._charts.sector = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: seriesColors(6), borderWidth: 0 }]
      },
      options: { responsive: true, plugins: { legend: { position: 'right', labels: { color: getThemeColor('--text-sub'), padding: 10, font: { size: 11 } } } } }
    });

    // 添加导出按钮
    const container = canvas.closest('.portfolio-card');
    if (container) {
      addExportButton(container, () => {
        exportChartJs(this._charts.sector, generateFilename('行业暴露'));
      }, '导出行业暴露图');
    }
  }

  _renderReturnVolChart(p) {
    const ctx = document.getElementById('returnVolChart')?.getContext('2d');
    if (!ctx) return;
    this._charts.returnVol?.destroy();
    this._charts.returnVol = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: ['预期收益', '预期波动'],
        datasets: [{
          data: [((p.expected_return || 0) * 100).toFixed(1), ((p.expected_vol || 0) * 100).toFixed(1)],
          backgroundColor: ['rgba(88,166,255,0.7)', 'rgba(210,153,34,0.7)'],
          borderRadius: 6,
        }]
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { ticks: { color: getThemeColor('--text-sub'), callback: v => v + '%' }, grid: { color: getThemeColor('--border') } }, x: { ticks: { color: getThemeColor('--text-sub') }, grid: { display: false } } } }
    });
  }

  _renderStats(p) {
    const stats = document.getElementById('portfolioStats');
    stats.textContent = '';
    const lines = [
      `预期年化收益: ${((p.expected_return || 0) * 100).toFixed(1)}%`,
      `预期年化波动: ${((p.expected_vol || 0) * 100).toFixed(1)}%`,
      `预期夏普比: ${(p.sharpe_ratio || 0).toFixed(2)}`,
      `持仓数量: ${p.n_positions || 0}`,
      `换手率: ${((p.turnover || 0) * 100).toFixed(1)}%`,
      `行业数: ${Object.keys(p.sector_exposure || {}).length}`,
    ];
    stats.textContent = lines.join('\n');
  }

  async _loadRegimeTimeline() {
    if (this._regimeLoaded) return;
    this._regimeLoaded = true;
    try {
      const data = await apiClient.get('/api/regime-history', { days: 60 });
      if (data && data.regimes) {
        this._lazy.whenVisible(
          () => this._renderRegimeTimeline(data.regimes),
          () => { this._charts.regime?.destroy(); this._charts.regime = null; }
        );
      }
    } catch (e) {
      console.error('[Portfolio] regime timeline failed:', e);
    }
  }

  _renderRegimeTimeline(regimes) {
    const canvas = document.getElementById('regimeTimelineChart');
    if (!canvas || !regimes.length) return;
    this._charts.regime?.destroy();

    const colors = {
      'TREND_UP': '#50c878', 'BULL': '#50c878',
      'TREND_DOWN': '#dc5050', 'BEAR': '#dc5050',
      'SIDEWAYS': '#ffa500', 'MEAN_REVERSION': '#ffa500',
      'VOLATILE': '#ff6b6b', 'HIGH_VOLATILITY': '#ff6b6b',
      'LOW_VOLATILITY': '#87ceeb',
      'RISK_OFF': '#dc5050', 'RECOVERY': '#50c878',
    };
    const regimeLabels = {
      'TREND_UP': '上升趋势', 'TREND_DOWN': '下跌趋势',
      'MEAN_REVERSION': '均值回归', 'HIGH_VOLATILITY': '高波动',
      'LOW_VOLATILITY': '低波动', 'RISK_OFF': '极端风险',
    };

    this._charts.regime = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: regimes.map(r => r.start_date),
        datasets: [{
          label: '市场状态',
          data: regimes.map(() => 1),
          backgroundColor: regimes.map(r => colors[r.regime] || '#999'),
          barPercentage: 0.9,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: ctx => {
                const r = regimes[ctx[0].dataIndex];
                return (regimeLabels[r.regime] || r.regime);
              },
              label: ctx => {
                const r = regimes[ctx.dataIndex];
                return `${r.start_date} ~ ${r.end_date}`;
              },
            },
          },
        },
        scales: {
          x: { display: false },
          y: { ticks: { font: { size: 10 }, color: getThemeColor('--text-sub') } },
        },
      },
    });
  }

  _updateEquityCurve(positionPnl) {
    if (!positionPnl) return;
    // 计算总市值
    let totalValue = 0;
    Object.values(positionPnl).forEach(p => {
      totalValue += (p.market_value || 0);
    });
    if (totalValue <= 0) return;

    // 追加到历史
    this._equityHistory = this._equityHistory || [];
    this._equityHistory.push({ time: Date.now(), value: totalValue });
    // 保留最近 100 个点
    if (this._equityHistory.length > 100) {
      this._equityHistory = this._equityHistory.slice(-100);
    }

    this._lazy.whenVisible(
      () => this._renderEquityCurve(),
      () => { this._charts.equity?.destroy(); this._charts.equity = null; }
    );
  }

  _renderEquityCurve() {
    const canvas = document.getElementById('equityCurveChart');
    if (!canvas || !this._equityHistory?.length) return;
    if (this._charts.equity) this._charts.equity.destroy();

    const labels = this._equityHistory.map((h, i) => i);
    const values = this._equityHistory.map(h => h.value);

    this._charts.equity = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: '总市值',
          data: values,
          borderColor: getThemeColor('--accent'),
          backgroundColor: 'rgba(88,166,255,0.1)',
          fill: true,
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          x: { display: false },
          y: {
            ticks: {
              color: getThemeColor('--text-sub'),
              callback: v => (v / 10000).toFixed(0) + '万',
            },
            grid: { color: getThemeColor('--border') },
          },
        },
      },
    });
  }

  unmount() {
    this._unsubscribers.forEach(unsub => unsub());
    this._vt?.destroy();
    this._vt = null;
    this._lazy.destroy();
    Object.values(this._charts).forEach(c => c?.destroy());
  }
}

export { PortfolioRenderer };
