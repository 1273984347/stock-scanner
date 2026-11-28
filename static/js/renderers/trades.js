import { apiClient } from '/js/api.js';
import { Pagination } from '/js/utils/pagination.js';

export class TradesRenderer {
  constructor() {
    this._allTrades = [];
    this._pagination = null;
  }

  mount() {
    const select = document.getElementById('tradeSortSelect');
    if (select) select.addEventListener('change', () => this.fetch());
    // 初始化分页
    const paginationContainer = document.getElementById('tradePagination');
    if (paginationContainer) {
      this._pagination = new Pagination(paginationContainer, {
        pageSize: 50,
        onPageChange: () => this._renderCurrentPage(),
      });
    }
    this.fetch();
  }

  async fetch() {
    try {
      const sortBy = document.getElementById('tradeSortSelect')?.value || 'date';
      const data = await apiClient.get('/api/trade-history', { days: 30, sort_by: sortBy, limit: 200 });
      if (data) {
        this._allTrades = data.trades || [];
        if (this._pagination) {
          this._pagination.setTotal(this._allTrades.length);
        }
        this._renderCurrentPage();
      }
    } catch (e) {
      console.error('[Trades] fetch failed:', e);
    }
  }

  _renderCurrentPage() {
    const tbody = document.getElementById('tradeHistoryBody');
    if (!tbody) return;
    while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

    if (!this._allTrades.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 8;
      td.textContent = '暂无交易记录';
      td.style.cssText = 'color:var(--text-dim);padding:16px;text-align:center';
      tr.appendChild(td);
      tbody.appendChild(tr);
      return;
    }

    const start = this._pagination ? this._pagination.startIndex : 0;
    const end = this._pagination ? this._pagination.endIndex : this._allTrades.length;
    const page = this._allTrades.slice(start, end);

    page.forEach(t => {
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
