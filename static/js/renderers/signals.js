import { apiClient } from '/js/api.js';
import { stateManager } from '/js/state.js';
import { Pagination } from '/js/utils/pagination.js';
import { LazyChart } from '/js/utils/lazyChart.js';
import { esc } from '/js/renderers/utils.js';

export class SignalsRenderer {
  constructor() {
    this.chart = null;
    this._trendChart = null;
    this._allRecent = [];
    this._pagination = null;
    this._lazy = new LazyChart('panel-signals');
  }

  mount() {
    stateManager.subscribe('signals', (data) => this.render(data));
    // 初始化分页
    const paginationContainer = document.getElementById('signalPagination');
    if (paginationContainer) {
      this._pagination = new Pagination(paginationContainer, {
        pageSize: 30,
        onPageChange: () => this._renderCurrentPage(),
      });
    }
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

    // 分页处理
    this._allRecent = recent || [];
    if (this._pagination) {
      this._pagination.setTotal(this._allRecent.length);
    }
    this._renderCurrentPage();

    if (by_source && Object.keys(by_source).length > 0) {
      this._lazy.whenVisible(
        () => {
          this._renderSourceChart(by_source);
          this._loadAccuracyTrend();
        },
        () => { this.chart?.destroy(); this.chart = null; this._trendChart?.destroy(); this._trendChart = null; }
      );
    }
  }

  _renderCurrentPage() {
    const tbody = document.getElementById('signalBody');
    if (!tbody) return;
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    if (!this._allRecent.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 7;
      td.textContent = '暂无信号记录';
      td.style.cssText = 'color:var(--text-dim);padding:16px;text-align:center';
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    const start = this._pagination ? this._pagination.startIndex : 0;
    const end = this._pagination ? this._pagination.endIndex : this._allRecent.length;
    const page = this._allRecent.slice(start, end);

    page.forEach(s => {
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

  async _loadAccuracyTrend() {
    try {
      const data = await apiClient.get('/api/signals/accuracy-trend', { days: 90 });
      if (data && data.trend) this._renderAccuracyTrendChart(data);
    } catch (e) {
      console.error('[Signals] accuracy trend failed:', e);
    }
  }

  _renderAccuracyTrendChart(data) {
    const canvas = document.getElementById('accuracyTrendChart');
    if (!canvas) return;
    if (this._trendChart) this._trendChart.destroy();

    this._trendChart = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: data.trend.map(t => t.week),
        datasets: [
          {
            label: '准确率(%)',
            data: data.trend.map(t => (t.accuracy * 100).toFixed(1)),
            borderColor: getThemeColor('--accent'),
            backgroundColor: 'rgba(88,166,255,0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 3,
          },
          {
            label: '信号数',
            data: data.trend.map(t => t.count),
            borderColor: '#ffa500',
            backgroundColor: 'transparent',
            tension: 0.3,
            pointRadius: 2,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'top', labels: { font: { size: 11 } } },
        },
        scales: {
          y: { beginAtZero: true, max: 100, position: 'left', title: { display: true, text: '准确率(%)' } },
          y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: '信号数' } },
        },
      },
    });
  }
}
