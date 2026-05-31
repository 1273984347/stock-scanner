export class Toolbar {
  constructor(container) {
    this.container = container;
    this.theme = 'light';
    this.timeframe = '1D';
    this.render();
    this.bindEvents();
  }

  render() {
    this.container.innerHTML = `
      <div class="toolbar-brand">Quant A-Share</div>
      <div class="toolbar-separator"></div>
      <div class="toolbar-group">
        <button class="tf-btn ${this.timeframe === '1D' ? 'active' : ''}" data-tf="1D">日线</button>
        <button class="tf-btn ${this.timeframe === '1W' ? 'active' : ''}" data-tf="1W">周线</button>
        <button class="tf-btn ${this.timeframe === '1M' ? 'active' : ''}" data-tf="1M">月线</button>
      </div>
      <div class="toolbar-separator"></div>
      <div class="toolbar-group">
        <button class="ind-btn" data-ind="MA">MA</button>
        <button class="ind-btn" data-ind="MACD">MACD</button>
        <button class="ind-btn" data-ind="RSI">RSI</button>
      </div>
      <div class="toolbar-spacer"></div>
      <button class="theme-toggle" title="切换主题">
        ${this.theme === 'dark' ? '☀️' : '🌙'}
      </button>
    `;
  }

  bindEvents() {
    this.container.addEventListener('click', (e) => {
      if (e.target.matches('.tf-btn')) {
        this.timeframe = e.target.dataset.tf;
        this.render();
      }
      if (e.target.matches('.theme-toggle')) {
        this.theme = this.theme === 'light' ? 'dark' : 'light';
        document.documentElement.setAttribute('data-theme', this.theme);
        this.render();
      }
    });
  }
}
