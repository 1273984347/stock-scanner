import { stateManager } from '/js/state.js';

/**
 * 色调切换 —— 保留元素原有类名，只增删色调类。
 *
 * 背景：TAPE 行情带的读数元素用 `.tape-value`，若直接覆盖 className 会
 * 把布局类抹掉，导致样式丢失（实测 probe 取不到 .tape-value）。
 */
function setTone(el, tone) {
  if (!el) return;
  el.classList.remove('text-up', 'text-down', 'text-warn');
  if (tone) el.classList.add(tone);
}

/**
 * MetricsRenderer — 6 个核心指标卡片渲染器
 * 订阅 state.dashboard.market.* 路径，变化时只更新对应卡片
 */
class MetricsRenderer {
  constructor() {
    this._unsubscribers = [];
  }

  mount() {
    // 订阅市场情绪
    this._unsubscribers.push(
      stateManager.subscribe('dashboard.market.sentiment', (v) => {
        document.getElementById('sentiment').textContent = v?.toFixed(2) ?? '--';
        const el = document.getElementById('sentiment');
        setTone(el, v > 0.2 ? 'text-up' : v < -0.2 ? 'text-down' : '');
        document.getElementById('sentimentLabel').textContent =
          v > 0.2 ? '偏多' : v < -0.2 ? '偏空' : '中性';
      })
    );

    // 订阅宏观门控
    this._unsubscribers.push(
      stateManager.subscribe('dashboard.market.macro_gate', (v) => {
        if (!v) return;
        const stateMap = { 'ALLOW_FULL': '通过', 'REDUCE_RISK': '减仓', 'BLOCK_NEW_RISK': '拦截' };
        const macroEl = document.getElementById('macroState');
        macroEl.textContent = stateMap[v.state] || v.state || '--';
        setTone(macroEl, v.state === 'BLOCK_NEW_RISK' ? 'text-down' : v.state === 'REDUCE_RISK' ? 'text-warn' : 'text-up');
        document.getElementById('macroLabel').textContent = v.reasoning || '';
      })
    );

    // 订阅仓位
    this._unsubscribers.push(
      stateManager.subscribe('dashboard.market.exposure', (v) => {
        if (!v) return;
        document.getElementById('exposure').textContent = ((v.exposure || 0.7) * 100).toFixed(0) + '%';
        const macro = stateManager.get('dashboard.market.macro_gate') || {};
        document.getElementById('exposureLabel').textContent = '仓位乘数: ' + ((macro.multiplier || 1) * 100).toFixed(0) + '%';
      })
    );

    // 订阅夏普比率
    this._unsubscribers.push(
      stateManager.subscribe('dashboard.portfolio', (v) => {
        if (!v) return;
        const sharpeEl = document.getElementById('sharpe');
        sharpeEl.textContent = v.sharpe_ratio != null ? v.sharpe_ratio.toFixed(2) : '--';
        setTone(sharpeEl, v.sharpe_ratio > 1 ? 'text-up' : v.sharpe_ratio > 0 ? '' : 'text-down');
        document.getElementById('nPositions').textContent = v.n_positions || 0;
      })
    );

    // 订阅候选股数
    this._unsubscribers.push(
      stateManager.subscribe('dashboard.total_candidates', (v) => {
        document.getElementById('totalCand').textContent = v || 0;
      })
    );
  }

  unmount() {
    this._unsubscribers.forEach(unsub => unsub());
    this._unsubscribers = [];
  }
}

export { MetricsRenderer };
