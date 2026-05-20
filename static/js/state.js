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
  }

  // 批量更新（用于 WebSocket 全量数据）
  updateAll(data) {
    const changedPaths = [];
    for (const [key, value] of Object.entries(data)) {
      const oldValue = this.get(key);
      if (!this._deepEqual(oldValue, value)) {
        this._updateSingle(key, value);
        changedPaths.push(key);
      }
    }
    // 只通知有变化的path对应的监听器
    changedPaths.forEach(path => {
      const callbacks = this._listeners.get(path);
      if (callbacks) {
        callbacks.forEach(cb => cb(this.get(path)));
      }
    });
  }

  _updateSingle(path, value) {
    const old = this.get(path);
    if (this._deepEqual(old, value)) return;
    this._dirty.add(path);
    this.set(path, value);
  }

  // 订阅路径变化
  subscribe(path, callback) {
    if (!this._listeners.has(path)) {
      this._listeners.set(path, new Set());
    }
    this._listeners.get(path).add(callback);
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
