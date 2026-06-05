export class Toolbar {
  constructor(container, onThemeChange) {
    this.container = container;
    this.onThemeChange = onThemeChange || null;
    this.themes = [
      { id: 'light', label: '亮色', icon: '☀️' },
      { id: 'dark', label: '暗色', icon: '🌙' },
      { id: 'high-contrast', label: '高对比度', icon: '🔲' }
    ];
    const saved = localStorage.getItem('theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    this.theme = saved || (prefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', this.theme);
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
    const brand = document.createElement('div');
    brand.className = 'toolbar-brand';
    brand.textContent = 'Quant A-Share';
    this.container.appendChild(brand);

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
    this.container.appendChild(breadcrumb);

    // Separator
    const sep2 = document.createElement('div');
    sep2.className = 'toolbar-separator';
    sep2.setAttribute('aria-hidden', 'true');
    this.container.appendChild(sep2);

    // Timeframe group
    const tfGroup = document.createElement('div');
    tfGroup.className = 'toolbar-group';
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
    indGroup.className = 'toolbar-group';
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
    themeBtn.textContent = currentTheme ? currentTheme.icon : '🌙';
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
      option.style.cssText = `padding:6px 10px;cursor:pointer;font-size:12px;display:flex;align-items:center;gap:6px;border-radius:4px;${this.theme === t.id ? 'background:var(--accent);color:#fff' : 'color:var(--text)'}`;
      option.textContent = `${t.icon} ${t.label}`;
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
