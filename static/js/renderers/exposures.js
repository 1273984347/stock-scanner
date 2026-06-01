import { stateManager } from '../state.js';
import { getThemeColor } from '../theme.js';

function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

class ExposuresRenderer {
  constructor() {
    this._chart = null;
    this._unsubscribers = [];
  }
  mount() {
    this._unsubscribers.push(stateManager.subscribe('exposures', (exp) => this.render(exp)));
  }
  render(exp) {
    if (!exp || !exp.exposures || !Object.keys(exp.exposures).length) {
      const body = document.getElementById('exposureBody');
      if (body) body.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--text-dim)">暂无因子暴露数据</td></tr>';
      return;
    }
    const exposures = exp.exposures || {};
    this._renderChart(exposures);
    this._renderTable(exposures);
  }
  _renderChart(exposures) {
    const ctx = document.getElementById('exposureChart')?.getContext('2d');
    if (!ctx) return;
    this._chart?.destroy();
    const keys = Object.keys(exposures);
    if (!keys.length) return;
    this._chart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: keys,
        datasets: [{ data: keys.map(k => Number(exposures[k]).toFixed(3)), backgroundColor: keys.map(k => Number(exposures[k]) > 0 ? 'rgba(63,185,80,0.6)' : 'rgba(248,81,73,0.6)'), borderRadius: 4 }]
      },
      options: { responsive: true, indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { ticks: { color: getThemeColor('--text-sub') }, grid: { color: getThemeColor('--border') } }, y: { ticks: { color: getThemeColor('--text-sub'), font: { size: 10 } }, grid: { display: false } } } }
    });
  }
  _renderTable(exposures) {
    const body = document.getElementById('exposureBody');
    body.innerHTML = '';
    Object.entries(exposures).forEach(([k, v]) => {
      const val = Number(v);
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${k}</td><td>${val.toFixed(4)}</td><td>${val > 0 ? '<span class="tag tag-up">偏多</span>' : val < 0 ? '<span class="tag tag-down">偏空</span>' : '<span class="tag" style="background:rgba(125,133,154,0.12);color:var(--text-sub)">中性</span>'}</td>`;
      body.appendChild(tr);
    });
  }
  unmount() { this._unsubscribers.forEach(u => u()); this._chart?.destroy(); }
}
export { ExposuresRenderer };
