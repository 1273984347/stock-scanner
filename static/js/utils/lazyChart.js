/**
 * LazyChart — 图表懒加载管理器
 * 面板不可见时暂停渲染，可见时恢复
 * 用法：const lazy = new LazyChart('panel-portfolio'); lazy.whenVisible(() => renderChart());
 */
class LazyChart {
  constructor(panelId) {
    this.panelId = panelId;
    this._visible = false;
    this._pendingRender = null;
    this._destroyFn = null;

    // 监听面板切换事件
    this._handler = (e) => {
      const isVisible = e.detail?.panelId === this.panelId;
      this._setVisible(isVisible);
    };
    document.addEventListener('panelchange', this._handler);

    // 检查初始状态
    const panel = document.getElementById(this.panelId);
    if (panel?.classList.contains('active')) {
      this._visible = true;
    }
  }

  get isVisible() {
    return this._visible;
  }

  /**
   * 注册渲染回调，面板可见时自动调用
   * @param {Function} renderFn - 渲染函数
   * @param {Function} [destroyFn] - 销毁函数（面板隐藏时调用以释放内存）
   */
  whenVisible(renderFn, destroyFn) {
    // 如果有旧的 destroyFn，先调用清理
    if (this._destroyFn && this._visible) {
      this._safeDestroy();
    }
    this._pendingRender = renderFn;
    this._destroyFn = destroyFn;
    // 如果当前可见，立即渲染
    if (this._visible) {
      this._safeRender();
    }
  }

  /**
   * 手动触发重新渲染（数据更新时调用）
   */
  refresh() {
    if (this._visible) {
      this._safeRender();
    }
  }

  _setVisible(visible) {
    if (visible === this._visible) return;
    this._visible = visible;
    if (visible) {
      this._safeRender();
    } else {
      this._safeDestroy();
    }
  }

  _safeRender() {
    if (this._pendingRender) {
      try {
        this._pendingRender();
      } catch (e) {
        console.error(`[LazyChart] ${this.panelId} render error:`, e);
      }
    }
  }

  _safeDestroy() {
    if (this._destroyFn) {
      try {
        this._destroyFn();
      } catch (e) {
        console.error(`[LazyChart] ${this.panelId} destroy error:`, e);
      }
    }
  }

  destroy() {
    document.removeEventListener('panelchange', this._handler);
    this._safeDestroy();
    this._pendingRender = null;
    this._destroyFn = null;
  }
}

export { LazyChart };
