import { apiClient } from './api.js';
import { stateManager } from './state.js';

/**
 * PollingController — WebSocket 降级轮询控制器
 * 当 WebSocket 不可用时，自动启动轮询
 * WebSocket 恢复时，自动停止轮询
 */
class PollingController {
  constructor() {
    // 轮询间隔配置
    this._intervals = {
      dashboard: 30_000,    // 核心指标 30s
      scan: 60_000,         // 选股结果 60s
      risk: 60_000,         // 风控 60s
      exposures: 60_000,    // 因子暴露 60s
    };
    this._timers = new Map();
    this._isPolling = false;
  }

  // 启动轮询（WebSocket 不可用时调用）
  start() {
    if (this._isPolling) return;
    this._isPolling = true;
    console.log('[Polling] 启动自动轮询降级');

    // dashboard 数据
    this._timers.set('dashboard', setInterval(() => {
      this._fetchDashboard();
    }, this._intervals.dashboard));

    // scan 数据
    this._timers.set('scan', setInterval(() => {
      this._fetchScan();
    }, this._intervals.scan));

    // risk 数据
    this._timers.set('risk', setInterval(() => {
      this._fetchRisk();
    }, this._intervals.risk));

    // exposures 数据
    this._timers.set('exposures', setInterval(() => {
      this._fetchExposures();
    }, this._intervals.exposures));

    // 立即执行一次
    this._fetchDashboard();
    this._fetchScan();
    this._fetchRisk();
    this._fetchExposures();
  }

  // 停止轮询（WebSocket 可用时调用）
  stop() {
    if (!this._isPolling) return;
    this._isPolling = false;
    console.log('[Polling] 停止自动轮询，切换到 WebSocket');
    this._timers.forEach((timer) => clearInterval(timer));
    this._timers.clear();
  }

  // 获取 dashboard 数据
  async _fetchDashboard() {
    try {
      const data = await apiClient.get('/api/dashboard');
      if (data) stateManager.set('dashboard', data);
    } catch (e) {
      console.error('[Polling] dashboard 获取失败:', e);
    }
  }

  // 获取 scan 数据
  async _fetchScan() {
    try {
      const data = await apiClient.get('/api/scan');
      if (data) stateManager.set('scan', data);
    } catch (e) {
      console.error('[Polling] scan 获取失败:', e);
    }
  }

  // 获取 risk 数据
  async _fetchRisk() {
    try {
      const data = await apiClient.get('/api/risk');
      if (data) stateManager.set('risk', data);
    } catch (e) {
      console.error('[Polling] risk 获取失败:', e);
    }
  }

  // 获取 exposures 数据
  async _fetchExposures() {
    try {
      const data = await apiClient.get('/api/exposures');
      if (data) stateManager.set('exposures', data);
    } catch (e) {
      console.error('[Polling] exposures 获取失败:', e);
    }
  }

  // 手动刷新（触发一次）
  async refreshAll() {
    await Promise.all([
      this._fetchDashboard(),
      this._fetchScan(),
      this._fetchRisk(),
      this._fetchExposures(),
    ]);
  }
}

const pollingController = new PollingController();
export { pollingController, PollingController };
