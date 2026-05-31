import { stateManager } from '../state.js';
import { KlineRenderer } from './kline.js';

/**
 * ScannerRenderer — 选股结果面板渲染器
 * 负责策略 Tab 切换 + 表格 + 信号分布 + 评分图表 + K线图
 */
class ScannerRenderer {
  constructor() {
    this._currentStrategy = '';
    this._sortCol = '';
    this._sortDir = 1;
    this._signalChart = null;
    this._factorChart = null;
    this._heatmapChart = null;
    this._kline = new KlineRenderer();
    this._currentKlineCode = '';
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
    this._loadSectorHeatmap();
    this._setupKlineDays();
  }

  _setupKlineDays() {
    const sel = document.getElementById('klineDays');
    if (sel) {
      sel.addEventListener('change', () => {
        if (this._currentKlineCode) {
          this._kline.loadKline(this._currentKlineCode, parseInt(sel.value, 10));
        }
      });
    }
  }

  async _loadSectorHeatmap() {
    try {
      const { apiClient } = await import('../api.js');
      const data = await apiClient.get('/api/sector-heatmap', { top_n: 50 });
      if (data && data.sectors) this._renderSectorHeatmap(data.sectors);
    } catch (e) {
      console.error('[Scanner] sector heatmap failed:', e);
    }
  }

  _renderSectorHeatmap(sectors) {
    const canvas = document.getElementById('sectorHeatmapChart');
    if (!canvas || !sectors.length) return;
    if (this._heatmapChart) this._heatmapChart.destroy();

    // Sort by score descending, take top 30
    const sorted = sectors.sort((a, b) => b.score - a.score).slice(0, 30);

    const colors = sorted.map(s => {
      if (s.score >= 70) return 'rgba(63, 185, 80, 0.8)';
      if (s.score >= 50) return 'rgba(255, 165, 0, 0.8)';
      if (s.score >= 30) return 'rgba(255, 107, 107, 0.6)';
      return 'rgba(220, 80, 80, 0.8)';
    });

    this._heatmapChart = new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels: sorted.map(s => s.name),
        datasets: [{
          label: '板块评分',
          data: sorted.map(s => s.score),
          backgroundColor: colors,
          borderWidth: 0,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              afterLabel: ctx => {
                const s = sorted[ctx.dataIndex];
                return [
                  `涨跌: ${s.return_pct?.toFixed(2)}%`,
                  `量比: ${s.volume_ratio?.toFixed(2)}`,
                  `涨停: ${s.limit_up_count}`,
                  `龙头: ${s.leader_code} (${s.leader_return?.toFixed(2)}%)`,
                ].join(' | ');
              },
            },
          },
        },
        scales: {
          x: {
            min: 0, max: 100,
            title: { display: true, text: '评分', color: '#7D8590' },
            ticks: { color: '#7D8590' },
            grid: { color: '#21262D' },
          },
          y: {
            ticks: { color: '#7D8590', font: { size: 10 } },
            grid: { display: false },
          },
        },
      },
    });
  }

  _renderStrategyTabs(results) {
    const container = document.getElementById('strategyTabs');
    if (!container) return;
    try {
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
    } catch (e) {
      console.error('[Scanner] 渲染策略标签失败:', e);
    }
  }

  _renderTable(data, strategyName) {
    const head = document.getElementById('resultHead');
    const body = document.getElementById('resultBody');
    if (!head || !body) return;
    try {
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
        tr.style.cursor = 'pointer';
        cols.forEach(c => {
          const td = document.createElement('td');
          const val = row[c];
          td.textContent = val == null ? '--' : typeof val === 'number' ? (Number.isInteger(val) ? String(val) : val.toFixed(2)) : String(val);
          tr.appendChild(td);
        });
        // Click row to load K-line chart
        tr.addEventListener('click', () => {
          const code = row['代码'] || row['code'] || '';
          if (code) {
            this._currentKlineCode = String(code);
            const title = document.getElementById('klineTitle');
            const name = row['名称'] || row['name'] || '';
            if (title) title.textContent = name ? `${name} (${code})` : code;
            const section = document.getElementById('klineSection');
            if (section) section.style.display = '';
            const days = parseInt(document.getElementById('klineDays')?.value || '120', 10);
            this._kline.loadKline(this._currentKlineCode, days);
          }
        });
        body.appendChild(tr);
      });
    } catch (e) {
      console.error('[Scanner] 渲染表格失败:', e);
      body.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:20px;color:var(--down)">渲染失败</td></tr>';
    }
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
    this._heatmapChart?.destroy();
    this._kline?.destroy();
  }
}

export { ScannerRenderer };
