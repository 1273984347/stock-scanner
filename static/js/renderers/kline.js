import { apiClient } from '/js/api.js';
import { getThemeColor } from '/js/theme.js';

/**
 * KlineRenderer — K线图渲染器
 * 使用 Chart.js 折线+柱状混合图模拟 K 线效果（收盘价线 + 成交量柱）
 * 支持交易标记叠加 + 日期范围缩放
 */
class KlineRenderer {
  constructor() {
    this._chart = null;
    this._allData = [];
    this._tradeData = [];
    this._currentCode = '';
  }

  async loadKline(code, days = 120) {
    this._currentCode = code;
    try {
      const [klineRes, tradeRes] = await Promise.all([
        apiClient.get(`/api/kline/${code}`, { days }),
        apiClient.get('/api/trade-history', { days, code, limit: 200 }).catch(() => null),
      ]);
      if (klineRes && klineRes.data && klineRes.data.length > 0) {
        this._allData = klineRes.data;
        this._tradeData = tradeRes?.trades || [];
        this._renderWithRange(0, this._allData.length);
        this._setupRangeSelector();
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

  _setupRangeSelector() {
    const container = document.getElementById('klineRangeContainer');
    if (!container) return;
    container.textContent = '';

    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 0;font-size:12px';

    const label = document.createElement('span');
    label.style.color = 'var(--text-dim)';
    label.textContent = '显示范围:';

    const rangeInput = document.createElement('input');
    rangeInput.type = 'range';
    rangeInput.min = '20';
    rangeInput.max = String(this._allData.length);
    rangeInput.value = String(this._allData.length);
    rangeInput.style.cssText = 'flex:1;accent-color:var(--accent)';

    const rangeLabel = document.createElement('span');
    rangeLabel.style.cssText = 'min-width:60px;text-align:right;color:var(--text-sub)';
    rangeLabel.textContent = this._allData.length + ' 天';

    rangeInput.addEventListener('input', () => {
      const n = parseInt(rangeInput.value, 10);
      rangeLabel.textContent = n + ' 天';
      this._renderWithRange(this._allData.length - n, this._allData.length);
    });

    // 快捷按钮
    const presets = [30, 60, 120, 250];
    presets.forEach(n => {
      if (n <= this._allData.length) {
        const btn = document.createElement('button');
        btn.textContent = n + '天';
        btn.style.cssText = 'padding:2px 8px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text);cursor:pointer;font-size:11px';
        btn.addEventListener('click', () => {
          rangeInput.value = String(n);
          rangeLabel.textContent = n + ' 天';
          this._renderWithRange(this._allData.length - n, this._allData.length);
        });
        wrapper.appendChild(btn);
      }
    });

    wrapper.appendChild(label);
    wrapper.appendChild(rangeInput);
    wrapper.appendChild(rangeLabel);
    container.appendChild(wrapper);
  }

  _renderWithRange(startIdx, endIdx) {
    const data = this._allData.slice(startIdx, endIdx);
    if (!data.length) return;
    this._renderKline(data, this._currentCode, startIdx);
  }

  _renderKline(data, code, offset = 0) {
    const canvas = document.getElementById('klineChart');
    if (!canvas) return;
    if (this._chart) this._chart.destroy();

    const labels = data.map(d => d.date);
    const closes = data.map(d => d.close);
    const volumes = data.map(d => d.volume);

    // Color candles: green if close >= open, red if close < open
    const colors = data.map(d => d.close >= d.open ? getThemeColor('--up') : getThemeColor('--down'));

    // Technical indicator data
    const ema5Data = data.map(d => d.ema5);
    const ema21Data = data.map(d => d.ema21);
    const ema50Data = data.map(d => d.ema50);
    const keltnerUpper = data.map(d => d.keltner_upper);
    const keltnerLower = data.map(d => d.keltner_lower);
    const donchianHigh = data.map(d => d.donchian_high);
    const donchianLow = data.map(d => d.donchian_low);

    // 交易标记：买入/卖出点
    const buyMarkers = new Array(data.length).fill(null);
    const sellMarkers = new Array(data.length).fill(null);
    const dateSet = new Set(labels);
    this._tradeData.forEach(t => {
      const date = t.timestamp?.slice(0, 10);
      if (date && dateSet.has(date)) {
        const idx = labels.indexOf(date);
        if (idx >= 0) {
          if (t.action === 'buy' || t.action === 'BUY') {
            buyMarkers[idx] = data[idx].low * 0.99;
          } else if (t.action === 'sell' || t.action === 'SELL') {
            sellMarkers[idx] = data[idx].high * 1.01;
          }
        }
      }
    });

    const datasets = [
      {
        label: `${code} 收盘价`,
        data: closes,
        type: 'line',
        borderColor: getThemeColor('--accent'),
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 3,
        pointHoverBackgroundColor: getThemeColor('--accent'),
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
      // 买入标记
      {
        label: '买入',
        data: buyMarkers,
        type: 'line',
        borderColor: 'transparent',
        pointRadius: buyMarkers.map(v => v !== null ? 8 : 0),
        pointBackgroundColor: getThemeColor('--up'),
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
        pointStyle: 'triangle',
        showLine: false,
        yAxisID: 'y',
        order: 0,
      },
      // 卖出标记
      {
        label: '卖出',
        data: sellMarkers,
        type: 'line',
        borderColor: 'transparent',
        pointRadius: sellMarkers.map(v => v !== null ? 8 : 0),
        pointBackgroundColor: getThemeColor('--down'),
        pointBorderColor: '#fff',
        pointBorderWidth: 2,
        pointStyle: 'rectRot',
        showLine: false,
        yAxisID: 'y',
        order: 0,
      },
      // EMA overlays (hidden by default)
      { label: 'EMA5', data: ema5Data, type: 'line', borderColor: '#ffa500', borderWidth: 1, pointRadius: 0, hidden: true, yAxisID: 'y', order: 3 },
      { label: 'EMA21', data: ema21Data, type: 'line', borderColor: '#ff6b6b', borderWidth: 1, pointRadius: 0, hidden: true, yAxisID: 'y', order: 3 },
      { label: 'EMA50', data: ema50Data, type: 'line', borderColor: '#9c27b0', borderWidth: 1, pointRadius: 0, hidden: true, yAxisID: 'y', order: 3 },
      // Keltner Channel (hidden by default)
      { label: 'Keltner上轨', data: keltnerUpper, type: 'line', borderColor: 'rgba(255,165,0,0.4)', borderWidth: 1, borderDash: [4,4], pointRadius: 0, hidden: true, yAxisID: 'y', order: 4 },
      { label: 'Keltner下轨', data: keltnerLower, type: 'line', borderColor: 'rgba(255,165,0,0.4)', borderWidth: 1, borderDash: [4,4], pointRadius: 0, hidden: true, yAxisID: 'y', order: 4 },
      // Donchian Channel (hidden by default)
      { label: 'Donchian高', data: donchianHigh, type: 'line', borderColor: 'rgba(80,200,120,0.4)', borderWidth: 1, borderDash: [4,4], pointRadius: 0, hidden: true, yAxisID: 'y', order: 4 },
      { label: 'Donchian低', data: donchianLow, type: 'line', borderColor: 'rgba(220,80,80,0.4)', borderWidth: 1, borderDash: [4,4], pointRadius: 0, hidden: true, yAxisID: 'y', order: 4 },
    ];

    this._chart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: { labels, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            display: true,
            position: 'top',
            labels: { color: getThemeColor('--text-sub'), font: { size: 11 }, padding: 12 },
          },
          tooltip: {
            backgroundColor: getThemeColor('--card'),
            titleColor: getThemeColor('--text'),
            bodyColor: getThemeColor('--text-sub'),
            borderColor: getThemeColor('--border'),
            borderWidth: 1,
            callbacks: {
              title: (items) => items[0]?.label || '',
              afterBody: (items) => {
                const idx = items[0]?.dataIndex;
                if (idx == null) return '';
                const d = data[idx];
                const lines = [
                  `开盘: ${d.open?.toFixed(2)}`,
                  `最高: ${d.high?.toFixed(2)}`,
                  `最低: ${d.low?.toFixed(2)}`,
                  `收盘: ${d.close?.toFixed(2)}`,
                ];
                // 显示交易信息
                const date = d.date;
                const trades = this._tradeData.filter(t => t.timestamp?.startsWith(date));
                if (trades.length > 0) {
                  lines.push('---');
                  trades.forEach(t => {
                    lines.push(`${t.action === 'buy' ? '买入' : '卖出'} ${t.shares}股 @ ${t.price?.toFixed(2)}`);
                  });
                }
                return lines;
              },
            },
          },
        },
        scales: {
          x: {
            ticks: {
              maxTicksLimit: 10,
              color: getThemeColor('--text-sub'),
              font: { size: 10 },
              maxRotation: 0,
            },
            grid: { color: getThemeColor('--border') },
          },
          y: {
            position: 'left',
            title: { display: true, text: '价格', color: getThemeColor('--text-sub') },
            ticks: { color: getThemeColor('--text-sub') },
            grid: { color: getThemeColor('--border') },
          },
          y1: {
            position: 'right',
            title: { display: true, text: '成交量', color: getThemeColor('--text-sub') },
            ticks: { color: getThemeColor('--text-sub') },
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
