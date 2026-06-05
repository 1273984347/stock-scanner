import { i18n } from '../i18n.js';

const NAV_ITEMS = [
  { id: 'portfolio', icon: '📊', labelKey: 'nav.portfolio' },
  { id: 'scanner', icon: '📈', labelKey: 'nav.scanner' },
  { id: 'risk', icon: '🛡️', labelKey: 'nav.risk' },
  { id: 'positions', icon: '💼', labelKey: 'nav.positions' },
  { id: 'exposures', icon: '🎯', labelKey: 'nav.exposures' },
  { id: 'signals', icon: '📡', labelKey: 'nav.signals' },
  { id: 'trades', icon: '💹', labelKey: 'nav.trades' },
  { id: 'backtest', icon: '⏱️', labelKey: 'nav.backtest' },
  { id: 'strategy', icon: '⚔️', labelKey: 'nav.strategy' },
  { id: 'audit', icon: '📋', labelKey: 'nav.audit' },
  { id: 'reports', icon: '📜', labelKey: 'nav.reports' },
  { id: 'settings', icon: '⚙️', labelKey: 'nav.settings' },
];

export class Sidebar {
  constructor(container, onNavigate) {
    this.container = container;
    this.onNavigate = onNavigate;
    this.activePanel = 'portfolio';
    this.render();
    this.bindEvents();
    // 监听语言变化
    i18n.onChange(() => this.render());
  }

  render() {
    this.container.setAttribute('role', 'navigation');
    this.container.setAttribute('aria-label', i18n.t('nav.portfolio'));
    this.container.innerHTML = '';
    NAV_ITEMS.forEach(item => {
      const div = document.createElement('div');
      div.className = `nav-item ${item.id === this.activePanel ? 'active' : ''}`;
      div.dataset.panel = item.id;
      div.title = i18n.t(item.labelKey);
      div.setAttribute('role', 'tab');
      div.setAttribute('aria-selected', String(item.id === this.activePanel));
      div.tabIndex = 0;

      const iconSpan = document.createElement('span');
      iconSpan.className = 'nav-icon';
      iconSpan.setAttribute('aria-hidden', 'true');
      iconSpan.textContent = item.icon;

      const labelSpan = document.createElement('span');
      labelSpan.className = 'nav-label';
      labelSpan.textContent = i18n.t(item.labelKey);

      div.appendChild(iconSpan);
      div.appendChild(labelSpan);
      this.container.appendChild(div);
    });
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
