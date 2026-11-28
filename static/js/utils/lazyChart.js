/**
 * LazyChart — 图表懒加载管理器
 * 面板不可见时暂停渲染，可见时恢复
 * 用法：const lazy = new LazyChart('panel-portfolio'); lazy.whenVisible(() => renderChart());
 */
/**
 * 全局图表尺寸兜底
 *
 * 现象：Chart.js 在容器宽高为 0 时初始化（面板 display:none），
 * 之后即使容器恢复尺寸，canvas 内部绘制缓冲仍停在 300×150 默认值 ⇒ 空白。
 * Chart.js 的 responsive 只在初始化时读取容器尺寸，不跟踪后续变化。
 *
 * 对策：用 ResizeObserver 观察所有 canvas 容器，尺寸变化时调用 chart.resize()。
 */
function installChartResizeObserver() {
  if (typeof Chart === 'undefined' || typeof ResizeObserver === 'undefined') return;
  if (installChartResizeObserver._done) return;
  installChartResizeObserver._done = true;

  const ro = new ResizeObserver((entries) => {
    if (!Chart.instances) return;
    for (const entry of entries) {
      const canvas = entry.target;
      if (!canvas || !canvas.tagName || canvas.tagName !== 'CANVAS') continue;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w <= 0 || h <= 0) continue;
      // 内部缓冲已是目标尺寸则无需重绘
      if (canvas.width === w && canvas.height === h) continue;
      const inst = Chart.getChart(canvas);
      if (!inst) continue;
      try { inst.resize(w, h); } catch (e) { /* 单图表失败不阻断其余 */ }
    }
  });

  const observeAll = () => {
    document.querySelectorAll('canvas').forEach(c => {
      if (!c.__roObserved) { c.__roObserved = true; ro.observe(c); }
    });
  };

  observeAll();
  // 面板切换会新增/显示 canvas，定期补观测
  document.addEventListener('panelchange', () => setTimeout(observeAll, 0));
  const mo = new MutationObserver(() => observeAll());
  mo.observe(document.body, { childList: true, subtree: true });
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installChartResizeObserver);
  } else {
    installChartResizeObserver();
  }
}

/**
 * 面板 id 比较：统一去掉 'panel-' 前缀。
 *
 * 事实：LazyChart 以 'panel-scanner' 构造，而 panelchange 事件的
 * detail.panelId 是 'scanner'（switchPanel 派发时未加前缀）。
 * 直接字符串比较永远为 false ⇒ 图表永不渲染（面板空白）。
 */
function _samePanel(a, b) {
  if (!a || !b) return false;
  const norm = (x) => String(x).replace(/^panel-/, '');
  return norm(a) === norm(b);
}

class LazyChart {
  constructor(panelId) {
    this.panelId = panelId;
    this._visible = false;
    // 同一面板可注册**多个**图表组（原实现只有一个槽位，
    // 后注册者会覆盖先注册者 ⇒ 先注册的图表永不渲染）
    this._pendingRenders = [];
    this._destroyFns = [];

    // 监听面板切换事件
    this._handler = (e) => {
      const isVisible = _samePanel(e.detail?.panelId, this.panelId);
      this._setVisible(isVisible);
    };
    document.addEventListener('panelchange', this._handler);

    // 检查初始状态
    //
    // 注意时序：AppLayout 在 DOMContentLoaded 之后才把面板 HTML 注入容器，
    // 而各 renderer 的 mount() 可能更早构造 LazyChart —— 此时 getElementById 返回 null，
    // 若就此判定「不可见」将永久失配（面板已激活却不再收到 panelchange）。
    // 对策：DOM 就绪后再复查一次。
    this._syncInitialVisibility();
  }

  _syncInitialVisibility() {
    const check = () => {
      const panel = document.getElementById(this.panelId);
      if (panel && panel.classList.contains('active') && !this._visible) {
        this._visible = true;
        this._safeRender();
      }
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', check, { once: true });
    } else {
      check();
    }
    // AppLayout 可能晚于 DOMContentLoaded 渲染（微任务/异步），再补一次延迟复查
    setTimeout(check, 0);
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
    // 移除同组的旧回调（同一渲染函数重复注册视为更新）
    const idx = this._pendingRenders.indexOf(renderFn);
    if (idx >= 0) {
      this._pendingRenders.splice(idx, 1);
      this._destroyFns.splice(idx, 1);
    }
    this._pendingRenders.push(renderFn);
    this._destroyFns.push(destroyFn || null);
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
    this._pendingRenders.slice().forEach(fn => {
      try {
        fn();
      } catch (e) {
        console.error(`[LazyChart] ${this.panelId} render error:`, e);
      }
    });
  }

  _safeDestroy() {
    this._destroyFns.slice().forEach(fn => {
      if (!fn) return;
      try {
        fn();
      } catch (e) {
        console.error(`[LazyChart] ${this.panelId} destroy error:`, e);
      }
    });
  }

  destroy() {
    document.removeEventListener('panelchange', this._handler);
    this._safeDestroy();
    this._pendingRenders = [];
    this._destroyFns = [];
  }
}

export { LazyChart };
