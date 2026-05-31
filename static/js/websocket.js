import { stateManager } from './state.js';
import { sendBrowserNotification } from './notifications.js';

/**
 * WebSocketManager — WebSocket 连接管理 + 心跳 + 断线重连
 * 职责：连接/重连/心跳/消息分发到 StateManager
 */
class WebSocketManager {
  constructor(url = `ws://${window.location.host}/ws`) {
    const apiKey = sessionStorage.getItem('api_key') || 'dev';
    this.url = url
      ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(apiKey)}`
      : `ws://${window.location.host}/ws?token=${encodeURIComponent(apiKey)}`;
    this._ws = null;
    this._reconnectDelay = 1000;  // 初始重连延迟 1s
    this._maxDelay = 30000;        // 最大重连延迟 30s
    this._heartbeatInterval = null;
    this._lastMessageTime = 0;
    this._isManualClose = false;
    this._onStatusChange = null;   // 状态变化回调
  }

  // 连接状态
  get status() {
    if (!this._ws) return 'disconnected';
    switch (this._ws.readyState) {
      case WebSocket.CONNECTING: return 'connecting';
      case WebSocket.OPEN: return 'connected';
      case WebSocket.CLOSING:
      case WebSocket.CLOSED: return 'disconnected';
    }
  }

  // 连接
  connect() {
    if (this._ws && this.status === 'connected') return;
    this._isManualClose = false;
    this._updateStatus('connecting');

    try {
      this._ws = new WebSocket(this.url);
    } catch (e) {
      console.error('[WS] 创建失败:', e);
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      console.log('[WS] 已连接');
      this._reconnectDelay = 1000; // 重置退造
      this._startHeartbeat();
      this._updateStatus('connected');
    };

    this._ws.onmessage = (event) => {
      this._lastMessageTime = Date.now();
      this._handleMessage(event.data);
    };

    this._ws.onerror = (e) => {
      console.error('[WS] 错误:', e);
      this._updateStatus('error');
    };

    this._ws.onclose = () => {
      console.log('[WS] 断开');
      this._stopHeartbeat();
      this._updateStatus('disconnected');
      if (!this._isManualClose) {
        this._scheduleReconnect();
      }
    };
  }

  // 断开
  disconnect() {
    this._isManualClose = true;
    this._ws?.close();
  }

  // 发送消息
  send(data) {
    if (this.status !== 'connected') return;
    this._ws.send(JSON.stringify(data));
  }

  // 消息处理
  _handleMessage(raw) {
    try {
      const msg = JSON.parse(raw);
      switch (msg.type) {
        case 'data_update':
          // 全量数据更新
          stateManager.updateAll(msg.data);
          // 单独分发 position_pnl 以确保订阅者收到实时更新
          if (msg.data?.position_pnl) {
            stateManager.set('position_pnl', msg.data.position_pnl);
            // 盈亏告警通知
            try {
              Object.entries(msg.data.position_pnl).forEach(([code, pnl]) => {
                if (pnl.pnl_pct < -0.05) {
                  sendBrowserNotification(`${code} 亏损告警`, `当前亏损 ${(pnl.pnl_pct * 100).toFixed(1)}%`, `alert-${code}`);
                }
              });
            } catch (_) { /* ignore notification errors */ }
          }
          // 新信号通知
          if (msg.data?.signal_summary?.total_signals > 0) {
            sendBrowserNotification('新信号', `${msg.data.signal_summary.total_signals} 条新信号`, 'signals');
          }
          break;
        case 'ping':
          this.send({ type: 'pong' });
          break;
        default:
          console.warn('[WS] 未知消息类型:', msg.type);
      }
    } catch (e) {
      console.error('[WS] 解析失败:', e);
    }
  }

  // 心跳
  _startHeartbeat() {
    this._stopHeartbeat(); // 先停止旧的
    this._heartbeatInterval = setInterval(() => {
      if (this.status === 'connected') {
        this.send({ type: 'pong' });
        // 超过 45s 无消息认为断连（心跳间隔1.5倍）
        if (Date.now() - this._lastMessageTime > 45000) {
          console.warn('[WS] 心跳超时，强制重连');
          this._ws?.close();
        }
      }
    }, 30000);
  }

  _stopHeartbeat() {
    if (this._heartbeatInterval) {
      clearInterval(this._heartbeatInterval);
      this._heartbeatInterval = null;
    }
  }

  // 重连计划（指数退造）
  _scheduleReconnect() {
    setTimeout(() => {
      if (!this._isManualClose) {
        console.log(`[WS] ${this._reconnectDelay}ms 后重连...`);
        this.connect();
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, this._maxDelay);
      }
    }, this._reconnectDelay);
  }

  // 状态变化回调
  _updateStatus(status) {
    this._onStatusChange?.(status);
  }

  onStatusChange(callback) {
    this._onStatusChange = callback;
  }
}

const wsManager = new WebSocketManager();
export { wsManager, WebSocketManager };
