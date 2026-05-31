export class ContextMenu {
  constructor() {
    this.menu = null;
    this.init();
  }

  init() {
    this.menu = document.createElement('div');
    this.menu.className = 'context-menu';
    this.menu.style.display = 'none';
    document.body.appendChild(this.menu);

    document.addEventListener('contextmenu', (e) => this.show(e));
    document.addEventListener('click', () => this.hide());
  }

  show(e) {
    e.preventDefault();

    const target = e.target.closest('[data-context]');
    if (!target) return;

    const items = this.getItems(target.dataset.context, target);
    if (!items.length) return;

    this.menu.innerHTML = items.map(item =>
      item.separator
        ? '<div class="context-separator"></div>'
        : '<div class="context-item" data-action="' + item.action + '">' + item.label + '</div>'
    ).join('');

    // Position menu
    const x = Math.min(e.clientX, window.innerWidth - 180);
    const y = Math.min(e.clientY, window.innerHeight - 200);
    this.menu.style.left = x + 'px';
    this.menu.style.top = y + 'px';
    this.menu.style.display = 'block';

    // Bind click events
    this.menu.querySelectorAll('.context-item').forEach(el => {
      el.addEventListener('click', () => {
        this.execute(el.dataset.action, target);
        this.hide();
      });
    });
  }

  hide() {
    this.menu.style.display = 'none';
  }

  getItems(type, target) {
    const menus = {
      stock: [
        { action: 'detail', label: '查看详情' },
        { action: 'watch', label: '添加到观察列表' },
        { separator: true },
        { action: 'export', label: '导出数据' },
      ],
      chart: [
        { action: 'add-indicator', label: '添加指标' },
        { action: 'change-timeframe', label: '切换周期' },
        { separator: true },
        { action: 'screenshot', label: '截图' },
      ],
      panel: [
        { action: 'fullscreen', label: '全屏' },
        { action: 'close', label: '关闭' },
        { separator: true },
        { action: 'reset-layout', label: '重置布局' },
      ],
    };
    return menus[type] || [];
  }

  execute(action, target) {
    target.dispatchEvent(new CustomEvent('context-action', {
      detail: { action },
      bubbles: true,
    }));
  }
}
