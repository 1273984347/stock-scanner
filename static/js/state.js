/**
 * StateManager — 统一状态存储 + 脏路径追踪 + 组件订阅
 * 职责：增量更新，只通知变化的组件
 */
class StateManager {
  constructor() {
    this._state = {};
    this._listeners = new Map(); // path -> Set<callback>
    this._dirty = new Set();
  }

  // 获取路径对应的值，支持 "a.b.c" 格式
  get(path) {
    if (!path) return this._state;
    return path.split('.').reduce((obj, key) => obj?.[key], this._state);
  }

  // 设置值，增量更新 + 脏标记
  set(path, value) {
    const keys = path.split('.');
    const last = keys.pop();
    const target = keys.reduce((obj, key) => {
      if (!(key in obj)) obj[key] = {};
      return obj[key];
    }, this._state);
    target[last] = value;
    // 通知该路径的订阅者
    this._notify(path);
    // 通知所有嵌套路径的订阅者（如 set('dashboard') 也要通知 'dashboard.market.sentiment'）
    for (const subPath of this._listeners.keys()) {
      if (subPath.startsWith(path + '.')) {
        this._notify(subPath);
      }
    }
  }

  _notify(path) {
    const callbacks = this._listeners.get(path);
    if (callbacks) {
      callbacks.forEach(cb => {
        try { cb(this.get(path)); } catch (e) { console.error('[State] 订阅者错误:', e); }
      });
    }
  }

  // 批量更新（用于 WebSocket 全量数据）
  updateAll(data) {
    for (const [key, value] of Object.entries(data)) {
      const oldValue = this.get(key);
      if (!this._deepEqual(oldValue, value)) {
        this._state[key] = value;
        this._dirty.add(key);
        this._notify(key);
      }
    }
  }

  // 深度合并更新（用于 WebSocket diff 数据）
  merge(path, data) {
    const keys = path.split('.');
    const last = keys.pop();
    const target = keys.reduce((obj, key) => {
      if (!(key in obj)) obj[key] = {};
      return obj[key];
    }, this._state);
    const oldValue = target[last];
    if (typeof oldValue === 'object' && typeof data === 'object' && oldValue !== null && data !== null && !Array.isArray(data)) {
      target[last] = { ...oldValue, ...data };
    } else {
      target[last] = data;
    }
    this._notify(path);
    for (const subPath of this._listeners.keys()) {
      if (subPath.startsWith(path + '.')) {
        this._notify(subPath);
      }
    }
  }

  // 订阅路径变化（立即调用一次回调，返回当前值）
  subscribe(path, callback) {
    console.log('[State.subscribe]', path);
    if (!this._listeners.has(path)) {
      this._listeners.set(path, new Set());
    }
    this._listeners.get(path).add(callback);
    // 立即调用一次回调，传入当前值（Redux 风格）
    const currentValue = this.get(path);
    if (currentValue !== undefined) {
      try { callback(currentValue); } catch (e) { console.error('[State] 订阅者错误:', e); }
    }
    // 返回取消订阅函数
    return () => this._listeners.get(path)?.delete(callback);
  }

  // 脏检查
  isDirty(path) {
    return this._dirty.has(path);
  }

  clearDirty(path) {
    this._dirty.delete(path);
  }

  clearAllDirty() {
    this._dirty.clear();
  }

  // 深度相等检查
  _deepEqual(a, b) {
    if (a === b) return true;
    if (a == null || b == null) return a === b;
    if (typeof a !== typeof b) return false;
    if (typeof a !== 'object') return a === b;
    if (Array.isArray(a) !== Array.isArray(b)) return false;
    const keysA = Object.keys(a);
    const keysB = Object.keys(b);
    if (keysA.length !== keysB.length) return false;
    return keysA.every(k => this._deepEqual(a[k], b[k]));
  }
}

// 导出单例
const stateManager = new StateManager();
export { stateManager, StateManager };
