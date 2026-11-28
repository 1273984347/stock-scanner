import { i18n } from '/js/i18n.js';

// 线性 SVG 图标（1px 描边）—— 不用 emoji：emoji 在终端界面里是廉价感的来源


// E 方案 · Apple Stocks 式：侧栏按工作流分组（扫描/组合/风险/记录）
const ICONS = {
  portfolio: '<path d="M3 3h6v8H3zM11 3h6v5h-6zM11 10h6v7h-6zM3 13h6v4H3z"/>',
  scanner:   '<path d="M3 12.5l3.5-4 2.8 2.4L14 5.5"/><path d="M14 5.5h-3.2M14 5.5v3.2"/>',
  risk:      '<path d="M10 2.5l6.5 2.6v4.4c0 3.5-2.7 6.2-6.5 7.6-3.8-1.4-6.5-4.1-6.5-7.6V5.1z"/>',
  positions: '<rect x="2.5" y="4" width="15" height="10" rx="2"/><path d="M2.5 8h15M7 4v10"/>',
  exposures: '<circle cx="10" cy="10" r="6.5"/><circle cx="10" cy="10" r="2.5"/>',
  signals:   '<circle cx="10" cy="10" r="2.2"/><path d="M4.8 4.8a7.4 7.4 0 000 10.4M15.2 4.8a7.4 7.4 0 010 10.4"/>',
  trades:    '<path d="M3 12l3.6-4 2.8 2.4L13.6 5.6"/><path d="M3 16h14"/>',
  backtest:  '<circle cx="10" cy="10" r="6.8"/><path d="M10 5.8V10l2.8 2"/>',
  strategy:  '<path d="M4.5 2.8v14.4M15.5 2.8v14.4M4.5 7h11M4.5 13h11"/>',
  audit:     '<path d="M5 2.8h10v14.4H5z"/><path d="M7.6 6.2h4.8M7.6 9h4.8M7.6 11.8h3"/>',
  reports:   '<rect x="3" y="2.8" width="14" height="14.4" rx="2"/><path d="M6 6.4h8M6 9.4h8M6 12.4h5"/>',
  notifications: '<path d="M10 3a4.2 4.2 0 00-4.2 4.2V10l-1.6 2.6h11.6L14.2 10V7.2A4.2 4.2 0 0010 3z"/><path d="M8.4 15.2a1.9 1.9 0 003.2 0"/>',
  settings:  '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.4v2.2M10 15.4v2.2M17.6 10h-2.2M4.6 10h-2.2M15.4 4.6l-1.6 1.6M6.2 13.8l-1.6 1.6M15.4 15.4l-1.6-1.6M6.2 6.2L4.6 4.6"/>'
};

const NAV_GROUPS = [
  { g: '扫描', items: [
    { id: 'scanner', labelKey: 'nav.scanner', count: '30' },
    { id: 'signals', labelKey: 'nav.signals', count: '92%' },
  ] },
  { g: '组合', items: [
    { id: 'portfolio', labelKey: 'nav.portfolio', count: '16' },
    { id: 'positions', labelKey: 'nav.positions', count: '65%' },
    { id: 'exposures', labelKey: 'nav.exposures', count: '9' },
  ] },
  { g: '风险', items: [
    { id: 'risk', labelKey: 'nav.risk', count: '3' },
    { id: 'backtest', labelKey: 'nav.backtest', count: '6' },
  ] },
  { g: '记录', items: [
    { id: 'trades', labelKey: 'nav.trades', count: '24' },
    { id: 'audit', labelKey: 'nav.audit', count: '15' },
    { id: 'reports', labelKey: 'nav.reports', count: '30' },
  ] },
  { g: '', items: [
    { id: 'strategy', labelKey: 'nav.strategy' },
    { id: 'notifications', labelKey: 'nav.notifications' },
    { id: 'settings', labelKey: 'nav.settings' },
  ] },
];

export class Sidebar {
  constructor(container, onNavigate) {
    this.container = container;
    this.onNavigate = onNavigate;
    this.activePanel = 'portfolio';
    // 展开态由点击切换（不再是 hover 改宽度 —— hover 会重建 DOM，
    // 导致元素句柄失效与视觉抖动）
    this.expanded = false;
    // E 方案：侧栏可折叠（点击空白处收起）
    this.collapsed = false;
    this.render();
    this.bindEvents();
    // 监听语言变化
    i18n.onChange(() => this.render());
  }

  render() {
    this.container.classList.toggle('collapsed', !!this.collapsed);
    this.container.setAttribute('role', 'navigation');
    this.container.setAttribute('aria-label', i18n.t('nav.portfolio'));
    this.container.innerHTML = '';
    NAV_GROUPS.forEach((group) => {
      if (group.g) {
        const head = document.createElement('div');
        head.className = 'sb-head';
        head.textContent = group.g;
        this.container.appendChild(head);
      }
      group.items.forEach((item) => {
        const div = document.createElement('div');
        div.className = 'sb-item' + (item.id === this.activePanel ? ' on' : '');
        div.dataset.panel = item.id;
        div.title = i18n.t(item.labelKey);
        div.setAttribute('role', 'tab');
        div.setAttribute('aria-selected', String(item.id === this.activePanel));
        div.tabIndex = 0;

        const iconSpan = document.createElement('span');
        iconSpan.className = 'nav-icon';
        iconSpan.setAttribute('aria-hidden', 'true');
        iconSpan.innerHTML = '<svg viewBox="0 0 20 20" focusable="false">' + (ICONS[item.id] || '') + '</svg>';

        const labelSpan = document.createElement('span');
        labelSpan.className = 'sb-label';
        labelSpan.textContent = i18n.t(item.labelKey);

        div.appendChild(iconSpan);
        div.appendChild(labelSpan);
        if (item.count) {
          const c = document.createElement('span');
          c.className = 'count';
          c.textContent = item.count;
          div.appendChild(c);
        }
        this.container.appendChild(div);
      });
    });
  }

  bindEvents() {
    this.container.addEventListener('click', (e) => {
      const item = e.target.closest('.nav-item');
      if (item) {
        this.activePanel = item.dataset.panel;
        // 窄栏点击导航项 ⇒ 展开显示完整标签；再次点击同项 ⇒ 收起
        this.expanded = this.expanded !== true ? true : this.activePanel === item.dataset.panel;
        this.render();
        if (this.onNavigate) this.onNavigate(this.activePanel);
      } else {
        // 点击侧栏空白处收起
        if (this.expanded) { this.expanded = false; this.render(); }
      }
    });
  }
}
