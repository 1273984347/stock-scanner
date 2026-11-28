/**
 * 主题图标：线性 SVG，不用 emoji（emoji 在终端界面里是廉价感的来源）
 */
const THEME_SVG = {
  dark:    '<path d="M14.2 10.4A6 6 0 018.6 4.8a6 6 0 105.6 5.6z"/>',
  light:   '<circle cx="9" cy="9" r="3.6"/><path d="M9 1.8v1.8M9 14.4v1.8M16.2 9h-1.8M3.6 9H1.8M14.1 3.9l-1.3 1.3M5.2 12.8l-1.3 1.3M14.1 14.1l-1.3-1.3M5.2 5.2L3.9 3.9"/>',
  system:  '<rect x="1.8" y="3" width="14.4" height="10" rx="1"/><path d="M6.6 16.2h4.8"/>',
};

export class Toolbar {
  constructor(container, onThemeChange) {
    this.container = container;
    this.onThemeChange = onThemeChange || null;
    // 三主题：深色 / 浅色 / 跟随系统（无高对比度）
    this.themes = [
      { id: 'dark', label: '深色' },
      { id: 'light', label: '浅色' },
      { id: 'system', label: '跟随系统' },
    ];
    const saved = localStorage.getItem('theme');
    this.theme = ['dark', 'light', 'system'].includes(saved) ? saved : 'system';
    document.documentElement.setAttribute('data-theme', this.theme);
    // 跟随系统时，系统主题变化需实时反映
    this._systemQuery = window.matchMedia('(prefers-color-scheme: dark)');
    if (this._systemQuery.addEventListener) {
      this._systemQuery.addEventListener('change', () => {
        if (this.theme !== 'system') return;
        document.documentElement.setAttribute('data-theme', 'system');
        if (this.onThemeChange) this.onThemeChange('system');
      });
    }
    this.timeframe = '1D';
    this._breadcrumb = ['首页'];
    this.render();
    this.bindEvents();
  }

  setBreadcrumb(path) {
    this._breadcrumb = ['首页', ...path];
    const bc = this.container.querySelector('.toolbar-breadcrumb');
    if (bc) bc.textContent = this._breadcrumb.join(' › ');
  }

  render() {
    this.container.setAttribute('role', 'toolbar');
    this.container.setAttribute('aria-label', '工具栏');
    this.container.innerHTML = '';

    // Brand
    const brandGroup = document.createElement('div');
    brandGroup.className = 'toolbar-group toolbar-brand-group';
    brandGroup.setAttribute('role', 'group');
    brandGroup.setAttribute('aria-label', '品牌与当前位置');
    const brand = document.createElement('div');
    brand.className = 'toolbar-brand';
    brand.textContent = 'Quant A-Share';
    brandGroup.appendChild(brand);

    // Separator
    const sep1 = document.createElement('div');
    sep1.className = 'toolbar-separator';
    sep1.setAttribute('aria-hidden', 'true');
    this.container.appendChild(sep1);

    // Breadcrumb
    const breadcrumb = document.createElement('div');
    breadcrumb.className = 'toolbar-breadcrumb';
    breadcrumb.style.cssText = 'font-size:12px;color:var(--text-dim)';
    breadcrumb.setAttribute('aria-label', '当前位置');
    breadcrumb.textContent = this._breadcrumb.join(' › ');
    brandGroup.appendChild(breadcrumb);
    this.container.appendChild(brandGroup);

    // Separator
    const sep2 = document.createElement('div');
    sep2.className = 'toolbar-separator';
    sep2.setAttribute('aria-hidden', 'true');
    this.container.appendChild(sep2);

    // Timeframe group
    const tfGroup = document.createElement('div');
    tfGroup.className = 'toolbar-group tf-group';
    tfGroup.setAttribute('role', 'group');
    tfGroup.setAttribute('aria-label', '时间周期');
    ['1D', '1W', '1M'].forEach(tf => {
      const btn = document.createElement('button');
      btn.className = `tf-btn ${this.timeframe === tf ? 'active' : ''}`;
      btn.dataset.tf = tf;
      btn.textContent = tf === '1D' ? '日线' : tf === '1W' ? '周线' : '月线';
      btn.setAttribute('aria-pressed', String(this.timeframe === tf));
      tfGroup.appendChild(btn);
    });
    this.container.appendChild(tfGroup);

    // Separator
    const sep3 = document.createElement('div');
    sep3.className = 'toolbar-separator';
    sep3.setAttribute('aria-hidden', 'true');
    this.container.appendChild(sep3);

    // Indicator group
    const indGroup = document.createElement('div');
    indGroup.className = 'toolbar-group ind-group';
    indGroup.setAttribute('role', 'group');
    indGroup.setAttribute('aria-label', '技术指标');
    ['MA', 'MACD', 'RSI'].forEach(ind => {
      const btn = document.createElement('button');
      btn.className = 'ind-btn';
      btn.dataset.ind = ind;
      btn.textContent = ind;
      indGroup.appendChild(btn);
    });
    this.container.appendChild(indGroup);

    // Spacer
    const spacer = document.createElement('div');
    spacer.className = 'toolbar-spacer';
    this.container.appendChild(spacer);

    // Theme selector
    const themeContainer = document.createElement('div');
    themeContainer.className = 'theme-container';
    themeContainer.style.cssText = 'position:relative;display:inline-block';

    const themeBtn = document.createElement('button');
    themeBtn.className = 'theme-toggle';
    themeBtn.title = '切换主题';
    themeBtn.setAttribute('aria-label', '切换主题');
    themeBtn.setAttribute('aria-haspopup', 'listbox');
    themeBtn.setAttribute('aria-expanded', 'false');
    const currentTheme = this.themes.find(t => t.id === this.theme);
    themeBtn.innerHTML = '<svg viewBox="0 0 18 18" focusable="false">' + (THEME_SVG[this.theme] || THEME_SVG.system) + '</svg>';
    themeBtn.title = currentTheme ? currentTheme.label : '主题';
    themeContainer.appendChild(themeBtn);

    const themeDropdown = document.createElement('div');
    themeDropdown.className = 'theme-dropdown';
    themeDropdown.setAttribute('role', 'listbox');
    themeDropdown.setAttribute('aria-label', '选择主题');
    themeDropdown.style.cssText = 'display:none;position:absolute;right:0;top:100%;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);padding:4px;z-index:100;min-width:120px;box-shadow:0 4px 12px rgba(0,0,0,0.15)';

    this.themes.forEach(t => {
      const option = document.createElement('div');
      option.className = 'theme-option';
      option.dataset.theme = t.id;
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(this.theme === t.id));
      const active = this.theme === t.id;
      option.style.cssText = `padding:6px 10px;cursor:pointer;font-size:11px;display:flex;align-items:center;gap:8px;border-radius:0;${active ? 'background:var(--accent);color:var(--accent-ink)' : 'color:var(--text-secondary)'}`;
      option.innerHTML = '<svg viewBox="0 0 18 18" focusable="false" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:1.4;stroke-linecap:round;stroke-linejoin:round">' + (THEME_SVG[t.id] || '') + '</svg><span>' + t.label + '</span>';
      themeDropdown.appendChild(option);
    });
    themeContainer.appendChild(themeDropdown);
    this.container.appendChild(themeContainer);
  }

  bindEvents() {
    this.container.addEventListener('click', (e) => {
      if (e.target.matches('.tf-btn')) {
        this.timeframe = e.target.dataset.tf;
        this.render();
      }
      if (e.target.matches('.theme-toggle')) {
        const dropdown = this.container.querySelector('.theme-dropdown');
        const isOpen = dropdown.style.display === 'block';
        dropdown.style.display = isOpen ? 'none' : 'block';
        e.target.setAttribute('aria-expanded', String(!isOpen));
      }
      if (e.target.matches('.theme-option')) {
        this.theme = e.target.dataset.theme;
        document.documentElement.setAttribute('data-theme', this.theme);
        localStorage.setItem('theme', this.theme);
        this.render();
        if (this.onThemeChange) this.onThemeChange(this.theme);
      }
    });

    // 点击外部关闭下拉菜单
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.theme-container')) {
        const dropdown = this.container.querySelector('.theme-dropdown');
        const toggle = this.container.querySelector('.theme-toggle');
        if (dropdown) dropdown.style.display = 'none';
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
      }
    });
  }
}
