import { stateManager } from '../state.js';

/**
 * ScannerRenderer — 选股结果面板渲染器
 * 负责策略 Tab 切换 + 表格 + 信号分布 + 评分图表
 */
class ScannerRenderer {
  constructor() {
    this._currentStrategy = '';
    this._sortCol = '';
    this._sortDir = 1;
    this._signalChart = null;
    this._factorChart = null;
    this._unsubscribers = [];
  }

  mount() {
    this._unsubscribers.push(
      stateManager.subscribe('scan.results', (results) => {
        if (!results || typeof results !== 'object') return;
        this._renderStrategyTabs(results);
        const first = Object.keys(results)[0];
        if (first && first !== this._currentStrategy) {
          this._currentStrategy = first;
          const items = results[first];
          if (Array.isArray(items) && items.length > 0) {
            this._renderTable(items, first);
            this._renderCharts(items, first);
          }
        }
      })
    );
  }

  _renderStrategyTabs(results) {
    const container = document.getElementById('strategyTabs');
    container.innerHTML = '';
    Object.entries(results).forEach(([name, items]) => {
      if (!Array.isArray(items)) return;
      const tab = document.createElement('div');
      tab.className = 'strategy-tab' + (name === this._currentStrategy ? ' active' : '');
      tab.textContent = `${name} (${items.length}${name === '热门板块' ? '板块' : '只'})`;
      tab.addEventListener('click', () => {
        this._currentStrategy = name;
        document.querySelectorAll('.strategy-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        this._sortCol = '';
        this._sortDir = 1;
        this._renderTable(items, name);
        this._renderCharts(items, name);
      });
      container.appendChild(tab);
    });
  }

  _renderTable(data, strategyName) {
    const head = document.getElementById('resultHead');
    const body = document.getElementById('resultBody');
    head.innerHTML = '';
    body.innerHTML = '';
    if (!Array.isArray(data) || !data.length) {
      body.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--text-dim)">无候选股</td></tr>';
      return;
    }
    if (!data[0]) return;
    const cols = Object.keys(data[0]);
    const headRow = document.createElement('tr');
    cols.forEach(c => {
      const th = document.createElement('th');
      th.textContent = c;
      th.addEventListener('click', () => this._sortTable(c, data, strategyName));
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    data.forEach(row => {
      const tr = document.createElement('tr');
      cols.forEach(c => {
        const td = document.createElement('td');
        const val = row[c];
        td.textContent = val == null ? '--' : typeof val === 'number' ? (Number.isInteger(val) ? String(val) : val.toFixed(2)) : String(val);
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
  }

  _sortTable(col, data, strategyName) {
    if (this._sortCol === col) this._sortDir *= -1;
    else { this._sortCol = col; this._sortDir = 1; }
    const sorted = [...data].sort((a, b) => {
      const va = a[col], vb = b[col];
      if (va == null) return 1;
      if (vb == null) return -1;
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * this._sortDir;
      return String(va).localeCompare(String(vb), 'zh') * this._sortDir;
    });
    this._renderTable(sorted, strategyName);
  }

  _renderCharts(data, name) {
    this._renderSignalChart(data);
    this._renderFactorChart(data);
  }

  _renderSignalChart(data) {
    const ctx = document.getElementById('signalChart')?.getContext('2d');
    if (!ctx) return;
    this._signalChart?.destroy();
    const verdicts = {};
    data.forEach(r => {
      const v = r['技术研判'] || 'N/A';
      verdicts[v] = (verdicts[v] || 0) + 1;
    });
    const labelMap = { 'bullish': '看多', 'bearish': '看空', 'neutral': '中性' };
    this._signalChart = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: Object.keys(verdicts).map(v => labelMap[v] || v),
        datasets: [{ data: Object.values(verdicts), backgroundColor: ['#3FB950', '#F85149', '#7D8590'], borderWidth: 0 }]
      },
      options: { responsive: true, plugins: { legend: { position: 'bottom', labels: { color: '#7D8590', padding: 12 } } } }
    });
  }

  _renderFactorChart(data) {
    const ctx = document.getElementById('factorChart')?.getContext('2d');
    if (!ctx) return;
    this._factorChart?.destroy();
    const top = data.slice(0, 10);
    const dk = top.some(r => r['综合评分'] != null) ? '综合评分' : '置信度';
    this._factorChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: top.map(r => r['名称'] || r['代码'] || ''),
        datasets: [{
          label: dk,
          data: top.map(r => typeof r[dk] === 'number' ? r[dk] * 100 : 0),
          backgroundColor: top.map(r => (r['涨跌幅'] || 0) > 0 ? 'rgba(63,185,80,0.7)' : 'rgba(248,81,73,0.7)'),
          borderRadius: 4
        }]
      },
      options: { responsive: true, indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#7D8590' }, grid: { color: '#21262D' } }, y: { ticks: { color: '#7D8590', font: { size: 11 } }, grid: { display: false } } } }
    });
  }

  unmount() {
    this._unsubscribers.forEach(unsub => unsub());
    this._signalChart?.destroy();
    this._factorChart?.destroy();
  }
}

export { ScannerRenderer };
