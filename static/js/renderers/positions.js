import { stateManager } from '../state.js';
import { fmtMoney } from './utils.js';

class PositionsRenderer {
  constructor() { this._unsubscribers = []; }
  mount() {
    this._unsubscribers.push(stateManager.subscribe('positions', (r) => this.render(r)));
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
    const body = document.getElementById('posBody');
    body.innerHTML = '';
    const details = r?.summary?.details || [];
    if (!details.length) { body.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-dim)">暂无持仓</td></tr>'; return; }
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
    const summary = r?.summary || {};
    document.getElementById('posSummary').textContent =
      `总持仓: ${summary.total_positions || 0} 只 | 总市值: ${fmtMoney(summary.total_market_value)} | 总盈亏: ${summary.total_pnl != null ? (summary.total_pnl > 0 ? '+' : '') + summary.total_pnl.toFixed(2) : '--'} | 仓位: ${summary.total_weight ? (summary.total_weight * 100).toFixed(1) + '%' : '--'}`;
  }
  unmount() { this._unsubscribers.forEach(u => u()); }
}
export { PositionsRenderer };
