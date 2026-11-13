import { apiClient } from '../api.js';
import { VirtualTable } from '../utils/virtualTable.js';

const TYPE_LABELS = {
  signal: '信号',
  trade: '交易',
  alert: '警报',
  system: '系统',
};

const TYPE_COLORS = {
  signal: '#4ecdc4',
  trade: '#5b8ff9',
  alert: '#f5a623',
  system: 'var(--text-dim)',
};

const LEVEL_COLORS = {
  info: 'var(--text-dim)',
  warning: '#f5a623',
  critical: '#ff6b6b',
};

export class NotificationsRenderer {
  constructor() {
    this._vt = null;
    this._bound = false;
  }

  mount() {
    if (this._bound) return;
    this._bound = true;
    const bind = (id, event) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener(event, () => this.fetch());
    };
    bind('notifTypeSelect', 'change');
    bind('notifStartDate', 'change');
    bind('notifEndDate', 'change');
    bind('notifKeyword', 'change');
    bind('notifRefreshBtn', 'click');
    this.fetch();
  }

  _readFilters() {
    const val = (id) => (document.getElementById(id)?.value || '').trim();
    const filters = { limit: 200, offset: 0 };
    const type = val('notifTypeSelect');
    const start = val('notifStartDate');
    const end = val('notifEndDate');
    const keyword = val('notifKeyword');
    if (type) filters.type = type;
    if (start) filters.start_date = start;
    if (end) filters.end_date = end;
    if (keyword) filters.keyword = keyword;
    return filters;
  }

  async fetch() {
    const loading = document.getElementById('notificationsLoading');
    try {
      if (loading) loading.style.display = '';
      const filters = this._readFilters();
      const data = await apiClient.get('/api/notifications', filters);
      this._renderRows(data.notifications || []);
      this._updateStats(data.total);
    } catch (e) {
      console.error('[Notifications] fetch failed:', e);
    } finally {
      if (loading) loading.style.display = 'none';
    }
    // 统计卡独立拉取（by_type 是全量口径，不随过滤器变化）
    try {
      const stats = await apiClient.get('/api/notifications/stats');
      if (stats && document.getElementById('notifStatByType')) {
        const parts = Object.entries(stats.by_type || {})
          .map(([t, n]) => `${TYPE_LABELS[t] || t} ${n}`)
          .join(' · ');
        document.getElementById('notifStatByType').textContent = parts || '暂无通知';
      }
    } catch (e) {
      console.debug('[Notifications] stats fetch failed:', e);
    }
  }

  _updateStats(total) {
    const el = document.getElementById('notifStatTotal');
    if (el) el.textContent = String(total ?? 0);
  }

  _renderRows(rows) {
    const tbody = document.getElementById('notificationsBody');
    if (!tbody) return;
    if (!this._vt) {
      this._vt = new VirtualTable(tbody, {
        rowHeight: 34,
        maxHeight: '58vh',
        emptyText: '暂无通知记录',
        renderRow: (i, n) => this._renderRow(n),
      });
    }
    this._vt.setData(rows);
  }

  _renderRow(n) {
    const tr = document.createElement('tr');

    const timeTd = document.createElement('td');
    const d = n.timestamp ? new Date(n.timestamp) : null;
    timeTd.textContent = d && !isNaN(d)
      ? d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
      : (n.timestamp || '--');
    timeTd.style.cssText = 'font-size:12px;white-space:nowrap';

    const typeTd = document.createElement('td');
    const typeSpan = document.createElement('span');
    typeSpan.textContent = TYPE_LABELS[n.type] || n.type || '--';
    typeSpan.style.cssText = `color:${TYPE_COLORS[n.type] || 'var(--text)'};font-size:12px`;
    typeTd.appendChild(typeSpan);
    typeTd.style.cssText = 'text-align:center';

    const levelTd = document.createElement('td');
    const levelSpan = document.createElement('span');
    levelSpan.textContent = n.level || 'info';
    levelSpan.style.cssText = `color:${LEVEL_COLORS[n.level] || 'var(--text-dim)'};font-size:12px`;
    levelTd.appendChild(levelSpan);
    levelTd.style.cssText = 'text-align:center';

    const titleTd = document.createElement('td');
    titleTd.textContent = n.title || '--';
    if (n.read === false) titleTd.style.fontWeight = '600';
    titleTd.style.cssText += ';max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';

    const bodyTd = document.createElement('td');
    bodyTd.textContent = n.body || '--';
    bodyTd.style.cssText = 'font-size:12px;color:var(--text-dim);max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';

    [timeTd, typeTd, levelTd, titleTd, bodyTd].forEach((td) => tr.appendChild(td));
    return tr;
  }
}
