import { apiClient } from '../api.js';

/**
 * KlineRenderer — K线图渲染器
 * 使用 Chart.js 折线+柱状混合图模拟 K 线效果（收盘价线 + 成交量柱）
 * 悬停 tooltip 显示完整 OHLC 数据
 */
class KlineRenderer {
  constructor() {
    this._chart = null;
  }

  async loadKline(code, days = 120) {
    try {
      const data = await apiClient.get(`/api/kline/${code}`, { days });
      if (data && data.data && data.data.length > 0) {
        this._renderKline(data.data, code);
      } else {
        this._showEmpty();
      }
    } catch (e) {
      console.error('[Kline] load failed:', e);
      this._showEmpty();
    }
  }

  _showEmpty() {
    const canvas = document.getElementById('klineChart');
    if (!canvas) return;
    if (this._chart) { this._chart.destroy(); this._chart = null; }
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }

  _renderKline(data, code) {
    const canvas = document.getElementById('klineChart');
    if (!canvas) return;
    if (this._chart) this._chart.destroy();

    const labels = data.map(d => d.date);
    const closes = data.map(d => d.close);
    const volumes = data.map(d => d.volume);

    // Color candles: green if close >= open, red if close < open
    const colors = data.map(d => d.close >= d.open ? '#3FB950' : '#F85149');

    this._chart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: `${code} 收盘价`,
            data: closes,
            type: 'line',
            borderColor: '#58A6FF',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            pointHoverRadius: 3,
            pointHoverBackgroundColor: '#58A6FF',
            yAxisID: 'y',
            order: 1,
          },
          {
            label: '成交量',
            data: volumes,
            type: 'bar',
            backgroundColor: colors.map(c => c + '40'),
            borderColor: colors,
            borderWidth: 1,
            yAxisID: 'y1',
            order: 2,
            barPercentage: 0.6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: { color: '#7D8590', font: { size: 11 }, padding: 12 },
          },
          tooltip: {
            backgroundColor: '#161B22',
            titleColor: '#E6EDF3',
            bodyColor: '#7D8590',
            borderColor: '#21262D',
            borderWidth: 1,
            callbacks: {
              title: (items) => items[0]?.label || '',
              afterBody: (items) => {
                const idx = items[0]?.dataIndex;
                if (idx == null) return '';
                const d = data[idx];
                return [
                  `开盘: ${d.open?.toFixed(2)}`,
                  `最高: ${d.high?.toFixed(2)}`,
                  `最低: ${d.low?.toFixed(2)}`,
                  `收盘: ${d.close?.toFixed(2)}`,
                ];
              },
            },
          },
        },
        scales: {
          x: {
            ticks: {
              maxTicksLimit: 10,
              color: '#7D8590',
              font: { size: 10 },
              maxRotation: 0,
            },
            grid: { color: '#21262D' },
          },
          y: {
            position: 'left',
            title: { display: true, text: '价格', color: '#7D8590' },
            ticks: { color: '#7D8590' },
            grid: { color: '#21262D' },
          },
          y1: {
            position: 'right',
            title: { display: true, text: '成交量', color: '#7D8590' },
            ticks: { color: '#7D8590' },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
  }

  destroy() {
    if (this._chart) { this._chart.destroy(); this._chart = null; }
  }
}

export { KlineRenderer };
