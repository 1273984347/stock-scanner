import { stateManager } from '../state.js';

class ExposuresRenderer {
  constructor() {
    this._chart = null;
    this._unsubscribers = [];
  }
  mount() {
    this._unsubscribers.push(stateManager.subscribe('exposures', (exp) => this.render(exp)));
  }
  render(exp) {
    if (!exp) return;
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
      options: { responsive: true, indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#7D8590' }, grid: { color: '#21262D' } }, y: { ticks: { color: '#7D8590', font: { size: 10 } }, grid: { display: false } } } }
    });
  }
  _renderTable(exposures) {
    const body = document.getElementById('exposureBody');
    body.innerHTML = '';
    Object.entries(exposures).forEach(([k, v]) => {
      const val = Number(v);
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${k}</td><td>${val.toFixed(4)}</td><td>${val > 0 ? '<span class="tag tag-up">偏多</span>' : val < 0 ? '<span class="tag tag-down">偏空</span>' : '<span class="tag" style="background:rgba(125,133,154,0.12);color:#7D8590">中性</span>'}</td>`;
      body.appendChild(tr);
    });
  }
  unmount() { this._unsubscribers.forEach(u => u()); this._chart?.destroy(); }
}
export { ExposuresRenderer };
