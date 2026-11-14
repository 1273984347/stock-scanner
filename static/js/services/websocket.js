class WebSocketService {
  constructor() {
    this.ws = null;
    this.reconnectTimer = null;
    this.listeners = new Map();
  }

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    // 批次 C / P1-2：凭据走子协议（不在 URL 里）。此前这一套实现完全不带 token，
    // 后端会以 4001 拒绝握手 —— 属于「两套 WS 实现」的遗留，这里先补齐可用性。
    const apiKey = sessionStorage.getItem('api_key') || 'dev';
    let subprotocols;
    try {
      const b64 = btoa(apiKey)
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
      subprotocols = [`bearer.${b64}`];
    } catch (e) {
      subprotocols = undefined;
    }

    this.ws = subprotocols ? new WebSocket(wsUrl, subprotocols) : new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('[WS] Connected');
      this.clearReconnect();
      this.notify({ type: 'status', status: 'connected' });
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.notify(data);
      } catch (e) {
        console.error('[WS] Parse error:', e);
      }
    };

    this.ws.onclose = () => {
      console.log('[WS] Disconnected');
      this.notify({ type: 'status', status: 'disconnected' });
      this.scheduleReconnect();
    };

    this.ws.onerror = (error) => {
      console.error('[WS] Error:', error);
      this.notify({ type: 'status', status: 'error' });
    };
  }

  scheduleReconnect() {
    this.clearReconnect();
    this.reconnectTimer = setTimeout(() => {
      console.log('[WS] Reconnecting...');
      this.connect();
    }, 3000);
  }

  clearReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  subscribe(type, callback) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set());
    }
    this.listeners.get(type).add(callback);
    return () => this.listeners.get(type)?.delete(callback);
  }

  notify(data) {
    const type = data.type;
    this.listeners.get(type)?.forEach(cb => cb(data));
    this.listeners.get('*')?.forEach(cb => cb(data));
  }

  send(data) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  disconnect() {
    this.clearReconnect();
    this.ws?.close();
  }
}

export const wsService = new WebSocketService();
