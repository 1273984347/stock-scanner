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
      }
    });
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
