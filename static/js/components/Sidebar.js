const NAV_ITEMS = [
  { id: 'portfolio', icon: '📊', label: '组合优化' },
  { id: 'scanner', icon: '📈', label: '选股结果' },
  { id: 'risk', icon: '🛡️', label: '风控中心' },
  { id: 'positions', icon: '💼', label: '持仓概况' },
  { id: 'factors', icon: '🎯', label: '因子分析' },
  { id: 'backtest', icon: '⏱️', label: '策略回测' },
  { id: 'audit', icon: '📋', label: '决策审计' },
  { id: 'reports', icon: '📜', label: '历史报告' },
  { id: 'settings', icon: '⚙️', label: '设置' },
];

export class Sidebar {
  constructor(container, onNavigate) {
    this.container = container;
    this.onNavigate = onNavigate;
    this.activePanel = 'portfolio';
    this.render();
    this.bindEvents();
  }

  render() {
    this.container.innerHTML = NAV_ITEMS.map(item => `
      <div class="nav-item ${item.id === this.activePanel ? 'active' : ''}" data-panel="${item.id}" title="${item.label}">
        <span class="nav-icon">${item.icon}</span>
        <span class="nav-label">${item.label}</span>
      </div>
    `).join('');
  }

  bindEvents() {
    this.container.addEventListener('click', (e) => {
      const item = e.target.closest('.nav-item');
      if (item) {
        this.activePanel = item.dataset.panel;
        this.render();
        if (this.onNavigate) this.onNavigate(this.activePanel);
      }
    });
  }
}
