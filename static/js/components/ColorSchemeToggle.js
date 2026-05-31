export class ColorSchemeToggle {
  constructor(container) {
    this.container = container;
    this.colorScheme = localStorage.getItem('colorScheme') || 'international';
    this.render();
    this.bindEvents();
    // Apply saved scheme on init
    document.documentElement.setAttribute('data-color-scheme', this.colorScheme);
  }

  render() {
    this.container.innerHTML = `
      <select class="color-scheme-select" title="涨跌配色">
        <option value="international" ${this.colorScheme === 'international' ? 'selected' : ''}>
          国际标准 (绿涨红跌)
        </option>
        <option value="china" ${this.colorScheme === 'china' ? 'selected' : ''}>
          A股标准 (红涨绿跌)
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
