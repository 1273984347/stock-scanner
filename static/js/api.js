/**
 * APIClient — REST API 请求封装
 * 职责：统一错误处理、超时、请求管理
 */
const API_BASE = '';

class APIClient {
  constructor(base = API_BASE) {
    this.base = base;
    this._abortControllers = new Map();
  }

  // 通用请求方法
  async request(endpoint, options = {}) {
    const { timeout = 60000, signal } = options;
    const controller = signal ? null : new AbortController();
    const timeoutId = setTimeout(() => controller?.abort(), timeout);

    try {
      const res = await fetch(this.base + endpoint, {
        ...options,
        signal: signal || controller?.signal,
      });
      clearTimeout(timeoutId);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      clearTimeout(timeoutId);
      if (e.name === 'AbortError') return null;
      throw e;
    }
  }

  // GET 请求
  get(endpoint, params = {}) {
    const qs = new URLSearchParams(params).toString();
    const url = qs ? `${endpoint}?${qs}` : endpoint;
    return this.request(url, { method: 'GET' });
  }

  // POST 请求
  post(endpoint, body = {}) {
    return this.request(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  }

  // 并行请求
  async all(requests) {
    return Promise.all(requests.map(([endpoint, params]) => this.get(endpoint, params)));
  }

  // 取消所有请求
  abortAll() {
    this._abortControllers.forEach(c => c.abort());
  }
}

const apiClient = new APIClient();
export { apiClient, APIClient };
