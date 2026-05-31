export class AppLayout {
  constructor(container) {
    this.container = container;
    this.render();
  }

  render() {
    this.container.innerHTML = `
      <div class="app-layout">
        <aside class="sidebar" id="sidebar"></aside>
        <div class="main-area">
          <header class="toolbar" id="toolbar"></header>
          <div class="content-area">
            <div class="chart-area" id="chart-area"></div>
            <aside class="right-panel" id="right-panel"></aside>
          </div>
          <footer class="statusbar" id="statusbar"></footer>
        </div>
      </div>
    `;
  }
}
