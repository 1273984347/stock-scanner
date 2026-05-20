import { stateManager } from '../state.js';

/**
 * PortfolioRenderer — 组合优化面板渲染器
 * 负责组合表格 + 行业饼图 + 收益波动柱状图
 */
class PortfolioRenderer {
  constructor() {
    this._sectorChart = null;
    this._returnVolChart = null;
    this._unsubscribers = [];
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
  }

  render(p) {
    if (!p) return;
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
    this._sectorChart?.destroy();
    const labels = Object.keys(sectors);
    const values = labels.map(k => Number(sectors[k]) * 100);
    if (!labels.length) return;
    this._sectorChart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: ['#58A6FF','#3FB950','#F85149','#D29922','#7D8590','#E6EDF3'], borderWidth: 0 }]
      },
      options: { responsive: true, plugins: { legend: { position: 'right', labels: { color: '#7D8590', padding: 10, font: { size: 11 } } } } }
    });
  }

  _renderReturnVolChart(p) {
    const ctx = document.getElementById('returnVolChart')?.getContext('2d');
    if (!ctx) return;
    this._returnVolChart?.destroy();
    this._returnVolChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: ['预期收益', '预期波动'],
        datasets: [{
          data: [((p.expected_return || 0) * 100).toFixed(1), ((p.expected_vol || 0) * 100).toFixed(1)],
          backgroundColor: ['rgba(88,166,255,0.7)', 'rgba(210,153,34,0.7)'],
          borderRadius: 6,
        }]
      },
      options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { ticks: { color: '#7D8590', callback: v => v + '%' }, grid: { color: '#21262D' } }, x: { ticks: { color: '#7D8590' }, grid: { display: false } } } }
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

  unmount() {
    this._unsubscribers.forEach(unsub => unsub());
    this._sectorChart?.destroy();
    this._returnVolChart?.destroy();
  }
}

export { PortfolioRenderer };
