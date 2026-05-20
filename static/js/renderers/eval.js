import { stateManager } from '../state.js';

class EvalRenderer {
  constructor() { this._unsubscribers = []; this._loaded = false; }
  mount() {
    // 懒加载：只有用户切换到 eval tab 时才请求数据
    const evalTab = document.querySelector('[data-panel="eval"]');
    if (evalTab) {
      evalTab.addEventListener('click', () => { if (!this._loaded) this._load(); });
    }
  }
  async _load() {
    this._loaded = true;
    document.getElementById('evalLoading').style.display = '';
    try {
      const r = await fetch('/api/factor-eval?n_stocks=30', { signal: AbortSignal.timeout(60000) }).then(res => res.json());
      document.getElementById('evalLoading').style.display = 'none';
      if (r.error) return;
      document.getElementById('evalInfo').textContent = `样本股: ${r.n_stocks||0}只 | 因子: ${Object.keys(r.factor_ic||{}).length}个 | 高相关对: ${(r.high_corr_pairs||[]).length}`;
      const icBody = document.getElementById('evalIcBody');
      icBody.innerHTML = '';
      Object.entries(r.factor_ic || {}).sort().forEach(([k, v]) => {
        const tr = document.createElement('tr');
        const icClass = typeof v.ic === 'number' ? (v.ic > 0 ? 'text-up' : v.ic < 0 ? 'text-down' : '') : '';
        tr.innerHTML = `<td>${k}</td><td class="${icClass}">${typeof v.ic === 'number' ? v.ic.toFixed(4) : '--'}</td><td>${(v.rank_ic || 0).toFixed(4)}</td><td>${v.pvalue < 0.01 ? '<span class="tag tag-up">' + v.pvalue.toFixed(4) + '</span>' : v.pvalue.toFixed(4)}</td>`;
        icBody.appendChild(tr);
      });
      const corrBody = document.getElementById('evalCorrBody');
      corrBody.innerHTML = '';
      (r.high_corr_pairs || []).forEach(p => {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${p.factor_a}</td><td>${p.factor_b}</td><td class="text-warn">${p.correlation.toFixed(3)}</td>`;
        corrBody.appendChild(tr);
      });
    } catch(e) { console.error(e); }
  }
  unmount() { this._unsubscribers.forEach(u => u()); }
}
export { EvalRenderer };
