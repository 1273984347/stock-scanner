import { apiClient } from '../api.js';

export class TradesRenderer {
  mount() {
    const select = document.getElementById('tradeSortSelect');
    if (select) select.addEventListener('change', () => this.fetch());
    this.fetch();
  }

  async fetch() {
    try {
      const sortBy = document.getElementById('tradeSortSelect')?.value || 'date';
      const data = await apiClient.get('/api/trade-history', { days: 30, sort_by: sortBy, limit: 50 });
      if (data) this.render(data.trades || []);
    } catch (e) {
      console.error('[Trades] fetch failed:', e);
    }
  }

  render(trades) {
    const tbody = document.getElementById('tradeHistoryBody');
    if (!tbody) return;
    // Clear existing rows safely
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    if (!trades.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 8;
      td.textContent = '暂无交易记录';
      td.style.cssText = 'color:var(--text-dim);padding:16px;text-align:center';
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    trades.forEach(t => {
      const pnl = (t.pnl_pct || 0) * 100;
      const tr = document.createElement('tr');
      tr.style.color = pnl >= 0 ? 'var(--up)' : 'var(--down)';

      const vals = [
        t.timestamp?.slice(0, 16) || '',
        t.code || '',
        t.action || '',
        (t.price || 0).toFixed(2),
        String(t.shares || 0),
        pnl.toFixed(2) + '%',
        String(t.hold_days || 0),
        t.exit_reason || '',
      ];
      vals.forEach(v => {
        const td = document.createElement('td');
        td.textContent = v;
        td.style.cssText = 'padding:4px 6px;border-bottom:1px solid var(--border)';
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
  }
}
