import { apiClient } from '../api.js';

/**
 * SearchBar — 全局搜索组件
 * Ctrl+K 触发，模糊匹配股票/板块/策略
 */
class SearchBar {
  constructor() {
    this._overlay = null;
    this._input = null;
    this._results = null;
    this._visible = false;
    this._init();
  }

  _init() {
    // 创建搜索弹窗
    this._overlay = document.createElement('div');
    this._overlay.id = 'searchOverlay';
    this._overlay.style.cssText = 'display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:1000;justify-content:center;padding-top:15vh';

    const dialog = document.createElement('div');
    dialog.style.cssText = 'width:500px;max-width:90vw;background:var(--card);border:1px solid var(--border);border-radius:12px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,0.3)';

    const header = document.createElement('div');
    header.style.cssText = 'display:flex;align-items:center;padding:12px 16px;border-bottom:1px solid var(--border)';

    const icon = document.createElement('span');
    icon.style.cssText = 'color:var(--text-dim);margin-right:8px;font-size:16px';
    icon.textContent = '🔍';

    this._input = document.createElement('input');
    this._input.type = 'text';
    this._input.placeholder = '搜索股票/板块/策略... (Esc 关闭)';
    this._input.style.cssText = 'flex:1;border:none;background:transparent;color:var(--text);font-size:14px;outline:none';

    const kbd = document.createElement('kbd');
    kbd.style.cssText = 'font-size:11px;padding:2px 6px;border:1px solid var(--border);border-radius:4px;color:var(--text-dim)';
    kbd.textContent = 'Ctrl+K';

    header.appendChild(icon);
    header.appendChild(this._input);
    header.appendChild(kbd);
    dialog.appendChild(header);

    this._results = document.createElement('div');
    this._results.style.cssText = 'max-height:300px;overflow-y:auto;padding:8px';
    dialog.appendChild(this._results);

    this._overlay.appendChild(dialog);
    document.body.appendChild(this._overlay);

    // 事件绑定
    this._input.addEventListener('input', () => this._onSearch());
    this._overlay.addEventListener('click', (e) => {
      if (e.target === this._overlay) this.hide();
    });

    // Ctrl+K 快捷键
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        this.toggle();
      }
      if (e.key === 'Escape' && this._visible) {
        this.hide();
      }
    });
  }

  toggle() {
    if (this._visible) this.hide();
    else this.show();
  }

  show() {
    this._visible = true;
    this._overlay.style.display = 'flex';
    this._input.value = '';
    this._input.focus();
    this._showDefaultResults();
  }

  hide() {
    this._visible = false;
    this._overlay.style.display = 'none';
  }

  _showDefaultResults() {
    // 显示最近访问和收藏
    const favorites = this._loadFavorites();
    const recent = this._loadRecent();

    this._results.textContent = '';

    if (favorites.length > 0) {
      this._addSection('收藏夹', favorites.slice(0, 5));
    }
    if (recent.length > 0) {
      this._addSection('最近访问', recent.slice(0, 5));
    }
    if (favorites.length === 0 && recent.length === 0) {
      const hint = document.createElement('div');
      hint.style.cssText = 'padding:16px;text-align:center;color:var(--text-dim);font-size:13px';
      hint.textContent = '输入股票代码或名称开始搜索';
      this._results.appendChild(hint);
    }
  }

  _addSection(title, items) {
    const label = document.createElement('div');
    label.style.cssText = 'font-size:11px;color:var(--text-dim);padding:4px 8px;text-transform:uppercase';
    label.textContent = title;
    this._results.appendChild(label);

    items.forEach(item => {
      this._addResultItem(item.code, item.name, item.type || 'stock');
    });
  }

  _addResultItem(code, name, type) {
    const item = document.createElement('div');
    item.style.cssText = 'display:flex;align-items:center;padding:8px 12px;border-radius:6px;cursor:pointer;font-size:13px;transition:background 0.15s';

    const icon = document.createElement('span');
    icon.style.cssText = 'margin-right:8px;font-size:12px';
    icon.textContent = type === 'sector' ? '📊' : type === 'strategy' ? '📈' : '💹';

    const info = document.createElement('div');
    info.style.cssText = 'flex:1';

    const nameEl = document.createElement('div');
    nameEl.style.fontWeight = '500';
    nameEl.textContent = name || code;

    const codeEl = document.createElement('div');
    codeEl.style.cssText = 'font-size:11px;color:var(--text-dim)';
    codeEl.textContent = code;

    info.appendChild(nameEl);
    info.appendChild(codeEl);
    item.appendChild(icon);
    item.appendChild(info);

    item.addEventListener('mouseenter', () => { item.style.background = 'var(--card-hover)'; });
    item.addEventListener('mouseleave', () => { item.style.background = 'transparent'; });
    item.addEventListener('click', () => {
      this.hide();
      this._addToRecent(code, name, type);
      if (type === 'stock') {
        window.openStockDetail?.(code, name);
      }
    });

    this._results.appendChild(item);
  }

  async _onSearch() {
    const query = this._input.value.trim();
    if (!query) {
      this._showDefaultResults();
      return;
    }

    // 本地搜索：收藏和最近
    const favorites = this._loadFavorites();
    const recent = this._loadRecent();
    const allLocal = [...favorites, ...recent];
    const matches = allLocal.filter(item =>
      item.code.includes(query) || (item.name && item.name.includes(query))
    );

    this._results.textContent = '';
    if (matches.length > 0) {
      this._addSection('本地匹配', matches.slice(0, 5));
    }

    // API 搜索（如果有足够字符，带 debounce）
    if (query.length >= 2) {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(async () => {
        try {
          const data = await apiClient.get('/api/stock/' + encodeURIComponent(query)).catch(() => null);
          if (data && data.code) {
            this._addResultItem(data.code, data.name || query, 'stock');
          }
        } catch (_) {}
      }, 300);
    }
  }

  // 收藏夹 localStorage
  _loadFavorites() {
    try {
      return JSON.parse(localStorage.getItem('search_favorites') || '[]');
    } catch { return []; }
  }

  _loadRecent() {
    try {
      return JSON.parse(localStorage.getItem('search_recent') || '[]');
    } catch { return []; }
  }

  _addToRecent(code, name, type) {
    let recent = this._loadRecent();
    recent = recent.filter(r => r.code !== code);
    recent.unshift({ code, name, type, time: Date.now() });
    if (recent.length > 20) recent = recent.slice(0, 20);
    localStorage.setItem('search_recent', JSON.stringify(recent));
  }
}

export { SearchBar };
