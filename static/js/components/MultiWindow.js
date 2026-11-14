/**
 * MultiWindow.js - 多窗口管理器
 * 支持在新窗口打开股票详情，通过 BroadcastChannel 同步数据
 */

const CHANNEL_NAME = 'stock-scanner-sync';
const WINDOW_PREFIX = 'stock-detail-';
// 批次 C / P2-11：跨窗口消息类型白名单（同源 + 白名单双闸）
const ALLOWED_MESSAGE_TYPES = new Set(['init', 'update', 'close', 'focus', 'sync']);

class MultiWindowManager {
  constructor() {
    this._windows = new Map(); // code -> Window
    this._channel = null;
    this._listeners = [];

    this._initChannel();
    this._bindCloseEvent();
  }

  _initChannel() {
    try {
      this._channel = new BroadcastChannel(CHANNEL_NAME);
      this._channel.onmessage = (e) => this._onMessage(e);
    } catch (err) {
      console.warn('[MultiWindow] BroadcastChannel not supported:', err);
    }
  }

  _bindCloseEvent() {
    window.addEventListener('beforeunload', () => {
      this._closeAllWindows();
    });
  }

  _onMessage(event) {
    // 批次 C / P2-11：只接受同源消息，且 type 必须在白名单内。
    // 旧实现直接把 event.data 转发给所有监听者 ⇒ 子窗口被导航到外部 origin
    // 时可向父窗口注入任意消息。
    if (event.origin !== window.location.origin) return;
    const { type, data } = event.data || {};
    if (typeof type !== 'string' || !ALLOWED_MESSAGE_TYPES.has(type)) return;
    if (data && data.code != null && !/^\d{6}$/.test(String(data.code))) return;
    this._listeners.forEach(cb => {
      try { cb(type, data); } catch (e) { console.error('[MultiWindow] Listener error:', e); }
    });
  }

  /**
   * 打开股票详情窗口
   * @param {string} code - 股票代码
   * @param {string} name - 股票名称
   * @param {Object} data - 股票数据
   */
  open(code, name, data = {}) {
    // 检查窗口是否已打开
    if (this._windows.has(code)) {
      const existingWin = this._windows.get(code);
      if (existingWin && !existingWin.closed) {
        existingWin.focus();
        // 发送最新数据
        this.sendData(code, data);
        return;
      }
      this._windows.delete(code);
    }

    // 打开新窗口
    const url = this._buildUrl(code);
    const windowFeatures = 'width=800,height=600,menubar=no,toolbar=no,location=no,status=no,resizable=yes,scrollbars=yes';

    const newWindow = window.open(url, WINDOW_PREFIX + code, windowFeatures);
    if (!newWindow) {
      console.error('[MultiWindow] Failed to open window (popup blocked?)');
      return;
    }

    this._windows.set(code, newWindow);

    // 等待窗口加载后发送数据
    const checkReady = setInterval(() => {
      try {
        if (newWindow.closed) {
          clearInterval(checkReady);
          this._windows.delete(code);
          return;
        }
        if (newWindow.document.readyState === 'complete') {
          clearInterval(checkReady);
          this._initChildWindow(newWindow, code, name, data);
        }
      } catch (e) {
        // 跨域限制，忽略
      }
    }, 100);

    // 清理定时器
    setTimeout(() => clearInterval(checkReady), 10000);
  }

  _buildUrl(code) {
    // 构建详情页 URL
    const base = window.location.origin;
    return `${base}/stock-detail.html?code=${code}`;
  }

  _initChildWindow(childWin, code, name, data) {
    try {
      // 发送初始化数据
      childWin.postMessage({
        type: 'init',
        data: { code, name, ...data }
      }, window.location.origin);  // P2-11：不用 '*'
    } catch (e) {
      console.warn('[MultiWindow] Cannot init child window:', e);
    }
  }

  /**
   * 向指定窗口发送数据
   * @param {string} code - 股票代码
   * @param {Object} data - 数据
   */
  sendData(code, data) {
    if (this._channel) {
      this._channel.postMessage({
        type: 'update',
        code,
        data
      });
    }

    // 也尝试直接 postMessage
    const win = this._windows.get(code);
    if (win && !win.closed) {
      try {
        win.postMessage({ type: 'update', code, data }, window.location.origin);  // P2-11
      } catch (e) {
        // 忽略跨域错误
      }
    }
  }

  /**
   * 广播数据到所有窗口
   * @param {string} type - 消息类型
   * @param {Object} data - 数据
   */
  broadcast(type, data) {
    if (this._channel) {
      this._channel.postMessage({ type, data });
    }
  }

  /**
   * 监听消息
   * @param {Function} callback - 回调函数
   */
  onMessage(callback) {
    this._listeners.push(callback);
    return () => {
      this._listeners = this._listeners.filter(l => l !== callback);
    };
  }

  /**
   * 关闭指定窗口
   * @param {string} code - 股票代码
   */
  close(code) {
    const win = this._windows.get(code);
    if (win && !win.closed) {
      win.close();
    }
    this._windows.delete(code);
  }

  /**
   * 关闭所有窗口
   */
  _closeAllWindows() {
    this._windows.forEach((win, code) => {
      if (win && !win.closed) {
        win.close();
      }
    });
    this._windows.clear();
  }

  /**
   * 获取打开的窗口列表
   * @returns {Array}
   */
  getOpenWindows() {
    const open = [];
    this._windows.forEach((win, code) => {
      if (win && !win.closed) {
        open.push(code);
      } else {
        this._windows.delete(code);
      }
    });
    return open;
  }

  /**
   * 检查窗口是否打开
   * @param {string} code - 股票代码
   * @returns {boolean}
   */
  isOpen(code) {
    const win = this._windows.get(code);
    if (win && !win.closed) return true;
    this._windows.delete(code);
    return false;
  }

  /**
   * 清理资源
   */
  destroy() {
    this._closeAllWindows();
    if (this._channel) {
      this._channel.close();
    }
    this._listeners = [];
  }
}

// 单例
export const multiWindowManager = new MultiWindowManager();
