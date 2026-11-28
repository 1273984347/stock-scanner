export class ColorSchemeToggle {
  constructor(container) {
    this.container = container;
    // 默认 A 股口径（红涨绿跌）
    this.colorScheme = localStorage.getItem('colorScheme') || 'china';
    this.render();
    this.bindEvents();
    // Apply saved scheme on init
    document.documentElement.setAttribute('data-color-scheme', this.colorScheme);
  }

  render() {
    this.container.innerHTML = `
      <select class="color-scheme-select" title="涨跌配色">
        <option value="china" ${this.colorScheme === 'china' ? 'selected' : ''}>
          A股 (红涨绿跌)
        </option>
        <option value="international" ${this.colorScheme === 'international' ? 'selected' : ''}>
          国际 (绿涨红跌)
        </option>
      </select>
    `;
  }

  bindEvents() {
    this.container.querySelector('select').addEventListener('change', (e) => {
      this.colorScheme = e.target.value;
      localStorage.setItem('colorScheme', this.colorScheme);
      document.documentElement.setAttribute('data-color-scheme', this.colorScheme);
    });
  }
}
