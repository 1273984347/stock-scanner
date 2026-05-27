import { stateManager } from '../state.js';

// Escape HTML special characters to prevent XSS
function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

class AuditRenderer {
  constructor() { this._unsubscribers = []; }
  mount() {
    this._unsubscribers.push(stateManager.subscribe('audit', (a) => this.render(a)));
  }
  render(audit) {
    const list = document.getElementById('auditList');
    list.innerHTML = '';
    const history = audit?.history || [];
    if (!history.length) { list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-dim)">暂无历史记录</div>'; return; }
    history.forEach(h => {
      const item = document.createElement('div');
      item.className = 'audit-item';
      item.addEventListener('click', () => openAuditDetail(h.id || h.scan_id || 0));
      item.innerHTML = `
        <span class="time">${esc((h.scan_time || '').substring(0, 19))}</span>
        <span class="count">${esc(h.total_candidates || 0)}只</span>
        <span>夏普: ${h.portfolio_sharpe != null ? esc(h.portfolio_sharpe.toFixed(2)) : '--'}</span>
        <span>风控: ${esc(h.risk_recommendation || '--')}</span>
        <span>持仓: ${esc(h.portfolio_n_positions || 0)}只</span>
      `;
      list.appendChild(item);
    });
  }
  unmount() { this._unsubscribers.forEach(u => u()); }
}
export { AuditRenderer };
