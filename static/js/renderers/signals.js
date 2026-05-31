import { apiClient } from '../api.js';
import { stateManager } from '../state.js';

function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export class SignalsRenderer {
  constructor() {
    this.chart = null;
  }

  mount() {
    stateManager.subscribe('signals', (data) => this.render(data));
  }

  async fetch() {
    try {
      const data = await apiClient.get('/api/signals', { days: 30 });
      if (data) stateManager.set('signals', data);
    } catch (e) {
      console.error('[Signals] fetch failed:', e);
    }
  }

  render(data) {
    if (!data) return;
    const { summary, recent, by_source } = data;

    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('sigTotal', summary.total_signals ?? '--');
    set('sigAccuracy', summary.accuracy != null ? (summary.accuracy * 100).toFixed(1) + '%' : '--');
    set('sigAvgReturn', summary.avg_return_5d != null ? (summary.avg_return_5d * 100).toFixed(2) + '%' : '--');
    set('sigPLRatio', summary.profit_loss_ratio != null ? summary.profit_loss_ratio.toFixed(2) : '--');

    const tbody = document.getElementById('signalBody');
    if (tbody && recent) {
      tbody.innerHTML = '';
      recent.slice(0, 30).forEach(s => {
        const tr = document.createElement('tr');
        const cells = [
          s.date || '',
          s.code || '',
          s.signal || '',
          s.confidence != null ? s.confidence.toFixed(2) : '',
          s.ic_score != null ? s.ic_score.toFixed(3) : '',
          s.actual_return_5d != null ? (s.actual_return_5d * 100).toFixed(2) + '%' : '-',
          s.is_correct === true ? '✓' : s.is_correct === false ? '✗' : '-',
        ];
        cells.forEach(txt => {
          const td = document.createElement('td');
          td.textContent = txt;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    if (by_source && Object.keys(by_source).length > 0) {
      this._renderSourceChart(by_source);
    }
  }

  _renderSourceChart(by_source) {
    const canvas = document.getElementById('signalSourceChart');
    if (!canvas) return;
    if (this.chart) this.chart.destroy();

    const labels = Object.keys(by_source);
    const accuracies = labels.map(l => (by_source[l].accuracy || 0) * 100);
    const counts = labels.map(l => by_source[l].count || 0);

    this.chart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [
          { label: '准确率(%)', data: accuracies, backgroundColor: 'rgba(75,192,192,0.6)', yAxisID: 'y' },
          { label: '信号数', data: counts, backgroundColor: 'rgba(255,159,64,0.6)', yAxisID: 'y1' },
        ],
      },
      options: {
        responsive: true,
        scales: {
          y: { beginAtZero: true, max: 100, position: 'left', title: { display: true, text: '准确率(%)' } },
          y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: '信号数' } },
        },
      },
    });
  }
}
