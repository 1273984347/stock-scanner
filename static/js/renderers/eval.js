import { getThemeColor, seriesColors } from '/js/theme.js';
import { stateManager } from '/js/state.js';
import { apiClient } from '/js/api.js';
import { LazyChart } from '/js/utils/lazyChart.js';
import { esc } from '/js/renderers/utils.js';

class EvalRenderer {
  constructor() {
    this._unsubscribers = [];
    this._loaded = false;
    this._charts = {};
    this._lazy = new LazyChart('panel-eval');
  }
  mount() {
    // 懒加载：只有用户切换到 eval tab 时才请求数据
    const evalTab = document.querySelector('[data-panel="eval"]');
    if (evalTab) {
      evalTab.addEventListener('click', () => {
        if (!this._loaded) this._load();
        this._loadFactorWeights();
        this._loadFactorScatter();
        this._loadICHistory();
      });
    }
  }
  async _load() {
    this._loaded = true;
    document.getElementById('evalLoading').style.display = '';
    try {
      const r = await apiClient.get('/api/factor-eval', { n_stocks: 30 });
      document.getElementById('evalLoading').style.display = 'none';
      if (r.error) return;
      document.getElementById('evalInfo').textContent = `样本股: ${r.n_stocks||0}只 | 因子: ${Object.keys(r.factor_ic||{}).length}个 | 高相关对: ${(r.high_corr_pairs||[]).length}`;
      const icBody = document.getElementById('evalIcBody');
      icBody.innerHTML = '';
      Object.entries(r.factor_ic || {}).sort().forEach(([k, v]) => {
        const tr = document.createElement('tr');
        const td0 = document.createElement('td'); td0.textContent = k;
        const td1 = document.createElement('td');
        if (typeof v.ic === 'number') td1.textContent = v.ic.toFixed(4);
        else td1.textContent = '--';
        td1.className = typeof v.ic === 'number' ? (v.ic > 0 ? 'text-up' : v.ic < 0 ? 'text-down' : '') : '';
        const td2 = document.createElement('td'); td2.textContent = (v.rank_ic || 0).toFixed(4);
        const td3 = document.createElement('td');
        if (v.pvalue != null) {
          if (v.pvalue < 0.01) {
            const span = document.createElement('span');
            span.className = 'tag tag-up';
            span.textContent = v.pvalue.toFixed(4);
            td3.appendChild(span);
          } else {
            td3.textContent = v.pvalue.toFixed(4);
          }
        } else {
          td3.textContent = '--';
        }
        tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3);
        icBody.appendChild(tr);
      });
      const corrBody = document.getElementById('evalCorrBody');
      corrBody.innerHTML = '';
      (r.high_corr_pairs || []).forEach(p => {
        const tr = document.createElement('tr');
        const td0 = document.createElement('td'); td0.textContent = p.factor_a;
        const td1 = document.createElement('td'); td1.textContent = p.factor_b;
        const td2 = document.createElement('td'); td2.textContent = p.correlation.toFixed(3); td2.className = 'text-warn';
        tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2);
        corrBody.appendChild(tr);
      });
    } catch(e) {
      document.getElementById('evalLoading').style.display = 'none';
      const icBody = document.getElementById('evalIcBody');
      icBody.innerHTML = '';
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4;
      td.style.cssText = 'color:var(--down);padding:20px;text-align:center';
      td.textContent = '加载失败，请稍后重试';
      tr.appendChild(td); icBody.appendChild(tr);
    }
  }
  async _loadFactorWeights() {
    try {
      const data = await apiClient.get('/api/factor-weights');
      if (data) this._renderWeightRadar(data);
    } catch (e) {
      console.error('[Eval] factor weights failed:', e);
    }
  }

  _renderWeightRadar(data) {
    const canvas = document.getElementById('weightRadarChart');
    if (!canvas || !data.ic_weights) return;
    if (this._charts.radar) this._charts.radar.destroy();

    const labels = Object.keys(data.ic_weights);
    const values = Object.values(data.ic_weights);

    this._charts.radar = new Chart(canvas.getContext('2d'), {
      type: 'radar',
      data: {
        labels,
        datasets: [{
          label: 'IC 因子权重',
          data: values,
          backgroundColor: 'rgba(79, 140, 255, 0.2)',
          borderColor: '#4f8cff',
          borderWidth: 2,
          pointRadius: 4,
          pointBackgroundColor: '#4f8cff',
        }],
      },
      options: {
        responsive: true,
        scales: {
          r: {
            beginAtZero: true,
            ticks: { font: { size: 10 } },
            pointLabels: { font: { size: 11 } },
          },
        },
        plugins: {
          legend: { position: 'top' },
        },
      },
    });
  }

  async _loadFactorScatter() {
    try {
      const data = await apiClient.get('/api/factor-scatter', { x_factor: 'volume_ratio', y_factor: 'adx', n_stocks: 30 });
      if (data && data.points) this._renderScatter(data);
    } catch (e) {
      console.error('[Eval] scatter failed:', e);
    }
  }

  _renderScatter(data) {
    const canvas = document.getElementById('factorScatterChart');
    if (!canvas || !data.points.length) return;
    if (this._charts.scatter) this._charts.scatter.destroy();

    this._charts.scatter = new Chart(canvas.getContext('2d'), {
      type: 'scatter',
      data: {
        datasets: [{
          label: `${data.x_factor} vs ${data.y_factor}`,
          data: data.points.map(p => ({ x: p.x, y: p.y })),
          backgroundColor: data.points.map(p => p.return >= 0 ? 'rgba(80,200,120,0.6)' : 'rgba(220,80,80,0.6)'),
          pointRadius: 6,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          tooltip: {
            callbacks: {
              label: ctx => {
                const p = data.points[ctx.dataIndex];
                return `${p.code}: ${data.x_factor}=${p.x}, ${data.y_factor}=${p.y}, 收益=${(p.return*100).toFixed(1)}%`;
              },
            },
          },
        },
        scales: {
          x: { title: { display: true, text: data.x_factor } },
          y: { title: { display: true, text: data.y_factor } },
        },
      },
    });
  }

  async _loadICHistory() {
    try {
      const data = await apiClient.get('/api/factor-ic-history', { days: 60 });
      if (!data || !data.dates || !data.factors) return;
      this._renderICHistoryChart(data);
    } catch (e) {
      console.error('[Eval] IC history failed:', e);
    }
  }

  _renderICHistoryChart(data) {
    const canvas = document.getElementById('icHistoryChart');
    if (!canvas) return;
    if (this._charts.icHistory) this._charts.icHistory.destroy();

    const colors = seriesColors(6);
    const datasets = Object.entries(data.factors).map(([name, values], i) => ({
      label: name,
      data: values,
      borderColor: colors[i % colors.length],
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.3,
    }));

    this._charts.icHistory = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { labels: data.dates, datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'top', labels: { font: { size: 11 } } },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          x: { ticks: { maxTicksLimit: 10, font: { size: 10 } } },
          y: { title: { display: true, text: 'IC 值' } },
        },
      },
    });
  }

  unmount() { this._unsubscribers.forEach(u => u()); }
}
export { EvalRenderer };
