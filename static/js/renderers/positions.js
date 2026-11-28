import { stateManager } from '/js/state.js';
import { apiClient } from '/js/api.js';
import { fmtMoney } from '/js/renderers/utils.js';
import { Pagination } from '/js/utils/pagination.js';

class PositionsRenderer {
  constructor() {
    this._unsubscribers = [];
    this._charts = {};
    this._allDetails = [];
    this._pagination = null;
  }
  mount() {
    this._unsubscribers.push(stateManager.subscribe('positions', (r) => this.render(r)));
    // 订阅 WebSocket 实时 P&L 更新
    this._unsubscribers.push(stateManager.subscribe('position_pnl', (data) => {
      if (data) this._updateRealtimePnL(data);
    }));
    // 事件委托：使用 data 属性而非 inline onclick
    document.getElementById('posBody')?.addEventListener('click', (e) => {
      const tr = e.target.closest('tr[data-code]');
      if (tr) {
        const code = tr.dataset.code;
        const name = tr.dataset.name || '';
        window.openStockDetail(code, name);
        this._showPositionDetail(code);
      }
    });
    // 初始化分页
    const paginationContainer = document.getElementById('posPagination');
    if (paginationContainer) {
      this._pagination = new Pagination(paginationContainer, {
        pageSize: 50,
        onPageChange: () => this._renderCurrentPage(),
      });
    }
    this._loadPositionDistribution();
  }
  async _showPositionDetail(code) {
    try {
      const data = await apiClient.get('/api/position/' + code);
      if (data) this._renderPositionDetail(data);
    } catch (e) {
      console.error('[Positions] detail failed:', e);
    }
  }
  _renderPositionDetail(data) {
    const container = document.getElementById('positionDetail');
    if (!container) return;
    container.textContent = '';

    const pnlColor = data.pnl_pct >= 0 ? 'var(--up)' : 'var(--down)';
    const pnlSign = data.pnl_pct >= 0 ? '+' : '';

    const wrap = document.createElement('div');
    wrap.style.cssText = 'background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-top:12px';

    // Header row
    const header = document.createElement('div');
    header.style.cssText = 'display:flex;justify-content:space-between;align-items:center;margin-bottom:10px';
    const title = document.createElement('h3');
    title.style.margin = '0';
    title.textContent = (data.name || '') + ' (' + data.code + ') 持仓详情';
    const closeBtn = document.createElement('span');
    closeBtn.style.cssText = 'cursor:pointer;color:var(--text-dim);font-size:18px';
    closeBtn.textContent = '×';
    closeBtn.addEventListener('click', () => { container.style.display = 'none'; });
    header.appendChild(title);
    header.appendChild(closeBtn);
    wrap.appendChild(header);

    // Grid
    const grid = document.createElement('div');
    grid.style.cssText = 'display:grid;grid-template-columns:repeat(4,1fr);gap:8px;font-size:13px';

    const addCell = (label, value, valueStyle) => {
      const cell = document.createElement('div');
      const lbl = document.createElement('span');
      lbl.style.color = 'var(--text-dim)';
      lbl.textContent = label + ': ';
      cell.appendChild(lbl);
      const val = document.createElement('b');
      if (valueStyle) val.style.cssText = valueStyle;
      val.textContent = value;
      cell.appendChild(val);
      grid.appendChild(cell);
    };

    addCell('成本', data.cost?.toFixed(2) || '--');
    addCell('现价', data.current_price?.toFixed(2) || '--');
    addCell('盈亏', pnlSign + (data.pnl_pct * 100).toFixed(2) + '%', 'color:' + pnlColor);
    addCell('盈亏额', pnlSign + fmtMoney(data.pnl_amount), 'color:' + pnlColor);
    addCell('股数', String(data.shares));
    addCell('市值', fmtMoney(data.market_value));
    addCell('止损', data.stop_loss ? (data.stop_loss * 100).toFixed(1) + '%' : '--');
    addCell('止盈', data.take_profit ? (data.take_profit * 100).toFixed(1) + '%' : '--');
    addCell('ATR止损价', data.atr_stop_price > 0 ? data.atr_stop_price.toFixed(2) : '--');
    addCell('移动止盈价', data.trailing_stop_price > 0 ? data.trailing_stop_price.toFixed(2) : '--');
    addCell('ATR止盈价', data.atr_take_profit_price > 0 ? data.atr_take_profit_price.toFixed(2) : '--');
    addCell('入场日期', data.entry_date || '--');
    addCell('持有天数', String(data.hold_days));
    addCell('ATR', data.atr > 0 ? data.atr.toFixed(4) + ' (x' + data.atr_mult + ')' : '--');
    addCell('最高价', data.peak_price > 0 ? data.peak_price.toFixed(2) : '--');
    addCell('备注', data.notes || '--');

    wrap.appendChild(grid);
    container.appendChild(wrap);
    container.style.display = 'block';
  }
  render(r) {
    this._allDetails = r?.summary?.details || [];
    if (this._pagination) {
      this._pagination.setTotal(this._allDetails.length);
    }
    this._renderCurrentPage();
    const summary = r?.summary || {};
    document.getElementById('posSummary').textContent =
      `总持仓: ${summary.total_positions || 0} 只 | 总市值: ${fmtMoney(summary.total_market_value)} | 总盈亏: ${summary.total_pnl != null ? (summary.total_pnl > 0 ? '+' : '') + summary.total_pnl.toFixed(2) : '--'} | 仓位: ${summary.total_weight ? (summary.total_weight * 100).toFixed(1) + '%' : '--'}`;
  }

  _renderCurrentPage() {
    const body = document.getElementById('posBody');
    while (body.firstChild) body.removeChild(body.firstChild);
    if (!this._allDetails.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 10;
      td.style.cssText = 'text-align:center;color:var(--text-dim)';
      td.textContent = '暂无持仓';
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    const start = this._pagination ? this._pagination.startIndex : 0;
    const end = this._pagination ? this._pagination.endIndex : this._allDetails.length;
    const page = this._allDetails.slice(start, end);
    this._renderRows(body, page);
  }

  _renderRows(body, details) {
    details.forEach(d => {
      const pnl = d.pnl || 0;
      const tr = document.createElement('tr');
      tr.dataset.code = d.code || '';
      tr.dataset.name = d.name || '';

      const codeTd = document.createElement('td');
      const codeLink = document.createElement('a');
      codeLink.style.cssText = 'color:var(--accent);cursor:pointer';
      codeLink.textContent = d.code || '--';
      codeTd.appendChild(codeLink);

      const nameTd = document.createElement('td');
      nameTd.textContent = d.name || '--';

      const sectorTd = document.createElement('td');
      sectorTd.textContent = d.sector || '--';

      const sharesTd = document.createElement('td');
      sharesTd.textContent = d.shares || 0;

      const costTd = document.createElement('td');
      costTd.textContent = d.cost_price ? d.cost_price.toFixed(2) : '--';

      const priceTd = document.createElement('td');
      priceTd.textContent = d.current_price ? d.current_price.toFixed(2) : '--';

      const pnlTd = document.createElement('td');
      pnlTd.className = pnl > 0 ? 'text-up' : pnl < 0 ? 'text-down' : '';
      pnlTd.textContent = (pnl > 0 ? '+' : '') + pnl.toFixed(2);

      const weightTd = document.createElement('td');
      weightTd.textContent = d.weight ? (d.weight * 100).toFixed(1) + '%' : '--';

      const stopTd = document.createElement('td');
      stopTd.textContent = d.stop_loss ? d.stop_loss.toFixed(2) : '--';

      const takeTd = document.createElement('td');
      takeTd.textContent = d.take_profit ? d.take_profit.toFixed(2) : '--';

      tr.appendChild(codeTd);
      tr.appendChild(nameTd);
      tr.appendChild(sectorTd);
      tr.appendChild(sharesTd);
      tr.appendChild(costTd);
      tr.appendChild(priceTd);
      tr.appendChild(pnlTd);
      tr.appendChild(weightTd);
      tr.appendChild(stopTd);
      tr.appendChild(takeTd);
      body.appendChild(tr);
    });
  }
  async _loadPositionDistribution() {
    try {
      const data = await apiClient.get('/api/position-distribution');
      if (data && data.by_sector) this._renderDistributionPie(data.by_sector);
    } catch (e) {
      console.error('[Positions] distribution failed:', e);
    }
  }
  _renderDistributionPie(bySector) {
    const canvas = document.getElementById('positionDistChart');
    if (!canvas) return;
    if (this._charts.dist) this._charts.dist.destroy();

    const entries = Object.entries(bySector).sort((a, b) => b[1] - a[1]);
    if (entries.length === 0) return;

    const colors = [
      '#4f8cff', '#50c878', '#ffa500', '#dc5050', '#9c27b0',
      '#00bcd4', '#ff9800', '#795548', '#607d8b', '#e91e63',
    ];

    this._charts.dist = new Chart(canvas.getContext('2d'), {
      type: 'pie',
      data: {
        labels: entries.map(([name]) => name),
        datasets: [{
          data: entries.map(([, v]) => (v * 100).toFixed(1)),
          backgroundColor: colors.slice(0, entries.length),
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'right', labels: { font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: ctx => ctx.label + ': ' + ctx.parsed + '%',
            },
          },
        },
      },
    });
  }
  _updateRealtimePnL(positionPnl) {
    const container = document.getElementById('realtimePnL');
    if (!container) return;

    const entries = Object.entries(positionPnl);
    if (entries.length === 0) {
      container.textContent = '暂无持仓';
      return;
    }

    // 批次 A / P0-2：股票代码曾被直接拼进 innerHTML。改用 DOM API，
    // 与本文件 _renderRows 已有的正确写法对齐，不依赖转义函数被记得调用。
    const grid = document.createElement('div');
    grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px';

    entries.forEach(([code, data]) => {
      const pnlPct = ((data.pnl_pct || 0) * 100).toFixed(2);
      const pnlColor = (data.pnl_pct || 0) >= 0 ? 'var(--up)' : 'var(--down)';
      const sign = (data.pnl_pct || 0) >= 0 ? '+' : '';

      const card = document.createElement('div');
      card.style.cssText = 'background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px;font-size:12px';
      const codeEl = document.createElement('b');
      codeEl.textContent = String(code);
      const pnlEl = document.createElement('span');
      pnlEl.style.color = pnlColor;
      pnlEl.textContent = `${sign}${pnlPct}%`;
      const priceEl = document.createElement('span');
      priceEl.style.color = 'var(--text-sub)';
      priceEl.textContent = data.current?.toFixed(2) || '-';
      card.appendChild(codeEl);
      card.appendChild(document.createTextNode(' '));
      card.appendChild(pnlEl);
      card.appendChild(document.createElement('br'));
      card.appendChild(priceEl);
      grid.appendChild(card);
    });

    container.replaceChildren(grid);
  }

  unmount() {
    this._unsubscribers.forEach(u => u());
    Object.values(this._charts).forEach(c => c.destroy());
    this._charts = {};
  }
}
export { PositionsRenderer };
