/**
 * Pagination — 可复用的分页组件
 * 用法：new Pagination(container, { pageSize: 50, onPageChange: (page) => ... })
 */
class Pagination {
  constructor(container, options = {}) {
    this.container = container;
    this.pageSize = options.pageSize || 50;
    this.currentPage = 1;
    this.totalItems = 0;
    this.onPageChange = options.onPageChange || (() => {});
    this._render();
  }

  get totalPages() {
    return Math.max(1, Math.ceil(this.totalItems / this.pageSize));
  }

  get startIndex() {
    return (this.currentPage - 1) * this.pageSize;
  }

  get endIndex() {
    return Math.min(this.startIndex + this.pageSize, this.totalItems);
  }

  setTotal(total) {
    this.totalItems = total;
    const oldPage = this.currentPage;
    if (this.currentPage > this.totalPages) {
      this.currentPage = this.totalPages;
    }
    this._render();
    if (this.currentPage !== oldPage) {
      this.onPageChange(this.currentPage);
    }
  }

  setPage(page) {
    const newPage = Math.max(1, Math.min(page, this.totalPages));
    if (newPage !== this.currentPage) {
      this.currentPage = newPage;
      this._render();
      this.onPageChange(this.currentPage);
    }
  }

  reset() {
    this.currentPage = 1;
    this.totalItems = 0;
    this._render();
  }

  _render() {
    if (!this.container) return;
    this.container.textContent = '';

    if (this.totalItems <= this.pageSize) {
      this.container.style.display = 'none';
      return;
    }
    this.container.style.display = 'flex';

    const nav = document.createElement('div');
    nav.style.cssText = 'display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-sub)';

    // 上一页
    const prevBtn = this._createBtn('‹', () => this.setPage(this.currentPage - 1));
    prevBtn.disabled = this.currentPage <= 1;
    nav.appendChild(prevBtn);

    // 页码按钮
    const pages = this._getPageNumbers();
    pages.forEach((p) => {
      if (p === '...') {
        const dots = document.createElement('span');
        dots.textContent = '...';
        dots.style.cssText = 'padding:0 4px;color:var(--text-dim)';
        nav.appendChild(dots);
      } else {
        const btn = this._createBtn(String(p), () => this.setPage(p));
        if (p === this.currentPage) {
          btn.style.cssText += 'background:var(--accent);color:#fff;border-color:var(--accent)';
        }
        nav.appendChild(btn);
      }
    });

    // 下一页
    const nextBtn = this._createBtn('›', () => this.setPage(this.currentPage + 1));
    nextBtn.disabled = this.currentPage >= this.totalPages;
    nav.appendChild(nextBtn);

    // 信息
    const info = document.createElement('span');
    info.style.cssText = 'margin-left:8px;color:var(--text-dim)';
    info.textContent = `${this.startIndex + 1}-${this.endIndex} / ${this.totalItems}`;
    nav.appendChild(info);

    // 每页条数选择
    const select = document.createElement('select');
    select.style.cssText = 'margin-left:8px;padding:2px 4px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text)';
    [20, 50, 100].forEach((n) => {
      const opt = document.createElement('option');
      opt.value = n;
      opt.textContent = `${n}/页`;
      opt.selected = n === this.pageSize;
      select.appendChild(opt);
    });
    select.addEventListener('change', () => {
      this.pageSize = parseInt(select.value, 10);
      this.currentPage = 1;
      this._render();
      this.onPageChange(this.currentPage);
    });
    nav.appendChild(select);

    this.container.appendChild(nav);
  }

  _createBtn(text, onClick) {
    const btn = document.createElement('button');
    btn.textContent = text;
    btn.style.cssText = 'min-width:28px;height:26px;padding:0 6px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text);cursor:pointer;font-size:12px';
    btn.addEventListener('click', onClick);
    btn.addEventListener('mouseenter', () => { if (!btn.disabled) btn.style.borderColor = 'var(--accent)'; });
    btn.addEventListener('mouseleave', () => { btn.style.borderColor = 'var(--border)'; });
    return btn;
  }

  _getPageNumbers() {
    const total = this.totalPages;
    const current = this.currentPage;
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);

    const pages = [1];
    if (current > 3) pages.push('...');
    for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
      pages.push(i);
    }
    if (current < total - 2) pages.push('...');
    pages.push(total);
    return pages;
  }
}

export { Pagination };
