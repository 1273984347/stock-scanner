/**
 * VirtualTable — 表格窗口化（虚拟滚动）组件
 *
 * 仅渲染可视区域内的行，解决「数据量 > 50 行时」一次性把全部 <tr> 塞进 DOM 导致的
 * 卡顿/内存膨胀问题（典型场景：选股结果表 300+ 行）。
 *
 * 设计要点：
 * - 保留真实 <table>/<thead>：列宽由 thead 决定，列对齐稳定，无需重写各 renderer 的单元格逻辑。
 * - spacer 行技术：顶部/底部各放一个占位的 <tr>（单 td，colspan 跨全列，指定高度），
 *   中间只渲染可视窗口内的真实行。滚动时仅重算窗口并重绘少量行。
 * - 滚动用 requestAnimationFrame 节流；容器尺寸变化用 ResizeObserver 监听。
 * - 数据量 <= threshold 时退化为普通全量渲染（不强制 max-height、不出现滚动条），
 *   契合 TODO「数据量 >50 行时」的语义，且小表视觉无变化。
 * - 表头 sticky：滚动时表头固定。每次刷新都重新应用（兼容 scanner 动态重建 thead）。
 *
 * 用法：
 *   const vt = new VirtualTable(tbodyEl, {
 *     rowHeight: 34,
 *     renderRow: (index, item) => { const tr = document.createElement('tr'); ...; return tr; },
 *     emptyText: '暂无数据',
 *     maxHeight: '58vh',         // 进入虚拟化时的滚动容器最大高度
 *     threshold: 50,             // 超过才虚拟化
 *     getColCount: () => 5,      // 可选，默认读 thead th 数量
 *   });
 *   vt.setData(items);           // 数据变化后调用
 *   vt.refresh();                // 容器 resize 等外部变化后手动刷新
 *   vt.destroy();                // 组件卸载时
 */
export class VirtualTable {
  constructor(tbody, options = {}) {
    if (!tbody) throw new Error('[VirtualTable] tbody is required');
    this.tbody = tbody;
    this.table = tbody.closest('table');
    this.scrollContainer = options.scrollContainer || tbody.closest('.table-wrap') || tbody.parentElement;
    this.rowHeight = options.rowHeight || 34;
    this.overscan = options.overscan || 8;
    this.renderRow = options.renderRow || (() => document.createElement('tr'));
    this.emptyText = options.emptyText || '暂无数据';
    this.maxHeight = options.maxHeight || '58vh';
    this.threshold = options.threshold != null ? options.threshold : 50;
    this.getColCount = typeof options.getColCount === 'function'
      ? options.getColCount
      : () => (this.table ? this.table.querySelectorAll('thead th').length : 1) || 1;

    this._data = [];
    this._virtualized = false;
    this._raf = 0;
    this._onScroll = this._onScroll.bind(this);
    this._onResize = this._onResize.bind(this);

    if (this.table) this.table.classList.add('vt-table');

    if (this.scrollContainer) {
      this.scrollContainer.addEventListener('scroll', this._onScroll, { passive: true });
    }
    if (typeof ResizeObserver !== 'undefined') {
      this._ro = new ResizeObserver(this._onResize);
      if (this.scrollContainer) this._ro.observe(this.scrollContainer);
    } else {
      window.addEventListener('resize', this._onResize);
    }
  }

  setData(data) {
    this._data = Array.isArray(data) ? data : [];
    this.refresh();
  }

  _onScroll() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      this._renderWindow();
    });
  }

  _onResize() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      this._renderWindow();
    });
  }

  refresh() {
    if (this._data.length > this.threshold) {
      this._enableVirtual();
      this._renderWindow();
    } else {
      this._renderAll();
    }
  }

  _enableVirtual() {
    if (this._virtualized) return;
    this._virtualized = true;
    if (this.scrollContainer) {
      this.scrollContainer.style.overflowY = 'auto';
      if (this.maxHeight) this.scrollContainer.style.maxHeight = this.maxHeight;
    }
  }

  _applyHeaderStyles() {
    if (!this.table) return;
    const ths = this.table.querySelectorAll('thead th');
    ths.forEach(th => {
      th.style.position = 'sticky';
      th.style.top = '0';
      th.style.zIndex = '3';
      if (!th.style.background) th.style.background = 'var(--card)';
    });
  }

  _renderAll() {
    // 退化模式：渲染全部行（小表不强制滚动）
    this._applyHeaderStyles();
    this.tbody.innerHTML = '';
    if (this._data.length === 0) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      const colCount = this.getColCount();
      td.colSpan = colCount;
      td.textContent = this.emptyText;
      td.style.cssText = 'text-align:center;color:var(--text-dim);padding:20px';
      tr.appendChild(td);
      this.tbody.appendChild(tr);
      return;
    }
    const frag = document.createDocumentFragment();
    for (let i = 0; i < this._data.length; i++) {
      frag.appendChild(this.renderRow(i, this._data[i], this._data.length));
    }
    this.tbody.appendChild(frag);
  }

  _renderWindow() {
    this._applyHeaderStyles();
    const total = this._data.length;
    if (total === 0) {
      this.tbody.innerHTML = '';
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = this.getColCount();
      td.textContent = this.emptyText;
      td.style.cssText = 'text-align:center;color:var(--text-dim);padding:20px';
      tr.appendChild(td);
      this.tbody.appendChild(tr);
      return;
    }

    const scrollTop = this.scrollContainer ? this.scrollContainer.scrollTop : 0;
    const viewportH = this.scrollContainer
      ? this.scrollContainer.clientHeight
      : (window.innerHeight || 600);
    const start = Math.max(0, Math.floor(scrollTop / this.rowHeight) - this.overscan);
    const visibleCount = Math.ceil(viewportH / this.rowHeight) + this.overscan * 2;
    const end = Math.min(total, start + visibleCount);
    const colCount = this.getColCount();

    const frag = document.createDocumentFragment();
    frag.appendChild(this._spacer(start * this.rowHeight, colCount));
    for (let i = start; i < end; i++) {
      frag.appendChild(this.renderRow(i, this._data[i], total));
    }
    frag.appendChild(this._spacer(Math.max(0, (total - end) * this.rowHeight), colCount));
    this.tbody.innerHTML = '';
    this.tbody.appendChild(frag);
  }

  _spacer(height, colCount) {
    const tr = document.createElement('tr');
    tr.className = 'vt-spacer';
    const td = document.createElement('td');
    td.colSpan = colCount;
    td.style.cssText = `height:${height}px;padding:0;border:0;line-height:0;font-size:0;`;
    tr.appendChild(td);
    return tr;
  }

  destroy() {
    if (this.scrollContainer) {
      this.scrollContainer.removeEventListener('scroll', this._onScroll);
      // 还原容器样式（仅还原本组件设置的项）
      this.scrollContainer.style.overflowY = '';
      if (this.maxHeight) this.scrollContainer.style.maxHeight = '';
    }
    if (this._ro) this._ro.disconnect();
    else window.removeEventListener('resize', this._onResize);
    if (this._raf) cancelAnimationFrame(this._raf);
    this._data = [];
    if (this.table) this.table.classList.remove('vt-table');
  }
}

export default VirtualTable;
