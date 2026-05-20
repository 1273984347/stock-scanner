import { stateManager } from '../state.js';
import { fmtMoney } from './utils.js';

class PositionsRenderer {
  constructor() { this._unsubscribers = []; }
  mount() {
    this._unsubscribers.push(stateManager.subscribe('positions', (r) => this.render(r)));
  }
  render(r) {
    const body = document.getElementById('posBody');
    body.innerHTML = '';
    const details = r?.summary?.details || [];
    if (!details.length) { body.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-dim)">暂无持仓</td></tr>'; return; }
    details.forEach(d => {
      const pnl = d.pnl || 0;
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><a onclick="openStockDetail('${d.code}','${d.name||''}')" style="color:var(--accent);cursor:pointer">${d.code}</a></td>
        <td>${d.name || '--'}</td>
        <td>${d.sector || '--'}</td>
        <td>${d.shares || 0}</td>
        <td>${d.cost_price ? d.cost_price.toFixed(2) : '--'}</td>
        <td>${d.current_price ? d.current_price.toFixed(2) : '--'}</td>
        <td class="${pnl > 0 ? 'text-up' : pnl < 0 ? 'text-down' : ''}">${pnl > 0 ? '+' : ''}${pnl.toFixed(2)}</td>
        <td>${d.weight ? (d.weight * 100).toFixed(1) + '%' : '--'}</td>
        <td>${d.stop_loss ? d.stop_loss.toFixed(2) : '--'}</td>
        <td>${d.take_profit ? d.take_profit.toFixed(2) : '--'}</td>
      `;
      body.appendChild(tr);
    });
    const summary = r?.summary || {};
    document.getElementById('posSummary').innerHTML = `
      总持仓: <b>${summary.total_positions || 0}</b> 只<br>
      总市值: <b>${fmtMoney(summary.total_market_value)}</b><br>
      总盈亏: <b>${summary.total_pnl != null ? (summary.total_pnl > 0 ? '+' : '') + summary.total_pnl.toFixed(2) : '--'}</b><br>
      仓位: <b>${summary.total_weight ? (summary.total_weight * 100).toFixed(1) + '%' : '--'}</b>
    `;
  }
  unmount() { this._unsubscribers.forEach(u => u()); }
}
export { PositionsRenderer };
