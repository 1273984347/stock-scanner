export class StatusBar {
  constructor(container) {
    this.container = container;
    this.wsStatus = 'disconnected';
    this.render();
  }

  render() {
    const statusClass = this.wsStatus === 'connected' ? 'connected' : 'disconnected';

    this.container.innerHTML = `
      <div class="status-item">
        <span class="status-dot ${statusClass}"></span>
        <span>WebSocket ${this.wsStatus === 'connected' ? '已连接' : '未连接'}</span>
      </div>
      <div class="status-item">数据源: TDX</div>
      <div class="status-item" id="last-update">最后更新: --:--:--</div>
      <div class="status-spacer"></div>
      <div class="status-item" id="index-info">沪深300: 加载中...</div>
    `;
  }

  setWsStatus(status) {
    this.wsStatus = status;
    this.render();
  }

  updateIndex(info) {
    const el = this.container.querySelector('#index-info');
    if (el) el.textContent = info;
  }
}
