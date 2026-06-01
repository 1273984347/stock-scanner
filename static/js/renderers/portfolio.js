import { stateManager } from '../state.js';
import { apiClient } from '../api.js';
import { getThemeColor } from '../theme.js';

/**
 * PortfolioRenderer — 组合优化面板渲染器
 * 负责组合表格 + 行业饼图 + 收益波动柱状图 + 市场状态时间线
 */
class PortfolioRenderer {
  constructor() {
    this._charts = { sector: null, returnVol: null, regime: null };
    this._unsubscribers = [];
    this._regimeLoaded = false;
  }

  mount() {
    this._unsubscribers.push(
      stateManager.subscribe('scan.portfolio', (data) => this.render(data))
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
    if (!p || !p.details || !p.details.length) {
      const body = document.getElementById('portfolioBody');
      if (body) body.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:20px">暂无组合数据，点击"刷新数据"开始扫描</td></tr>';
      return;
    }
    this._renderTable(p.details || []);
    this._renderSectorChart(p.sector_exposure || {});
    this._renderReturnVolChart(p);
    this._renderStats(p);
  }

  _renderTable(details) {
    const body = document.getElementById('portfolioBody');
    body.innerHTML = '';
    details.forEach(d => {
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
      body.appendChild(tr);
    });
  }

  _renderSectorChart(sectors) {
    const ctx = document.getElementById('sectorChart')?.getContext('2d');
    if (!ctx) return;
    this._charts.sector?.destroy();
    const labels = Object.keys(sectors);
    const values = labels.map(k => Number(sectors[k]) * 100);
    if (!labels.length) return;
    this._charts.sector = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: ['#58A6FF','#3FB950','#F85149','#D29922','#7D8590','#E6EDF3'], borderWidth: 0 }]
      },
      options: { responsive: true, plugins: { legend: { position: 'right', labels: { color: getThemeColor('--text-sub'), padding: 10, font: { size: 11 } } } } }
    });
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
      if (data && data.regimes) this._renderRegimeTimeline(data.regimes);
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

  unmount() {
    this._unsubscribers.forEach(unsub => unsub());
    Object.values(this._charts).forEach(c => c?.destroy());
  }
}

export { PortfolioRenderer };
