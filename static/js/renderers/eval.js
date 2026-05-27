import { stateManager } from '../state.js';

function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

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
        const td0 = document.createElement('td'); td0.textContent = k;
        const td1 = document.createElement('td');
        if (typeof v.ic === 'number') td1.textContent = v.ic.toFixed(4);
        else td1.textContent = '--';
        td1.className = typeof v.ic === 'number' ? (v.ic > 0 ? 'text-up' : v.ic < 0 ? 'text-down' : '') : '';
        const td2 = document.createElement('td'); td2.textContent = (v.rank_ic || 0).toFixed(4);
        const td3 = document.createElement('td');
        if (v.pvalue != null) {
          if (v.pvalue < 0.01) {
            const span = document.createElement('span');
            span.className = 'tag tag-up';
            span.textContent = v.pvalue.toFixed(4);
            td3.appendChild(span);
          } else {
            td3.textContent = v.pvalue.toFixed(4);
          }
        } else {
          td3.textContent = '--';
        }
        tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3);
        icBody.appendChild(tr);
      });
      const corrBody = document.getElementById('evalCorrBody');
      corrBody.innerHTML = '';
      (r.high_corr_pairs || []).forEach(p => {
        const tr = document.createElement('tr');
        const td0 = document.createElement('td'); td0.textContent = p.factor_a;
        const td1 = document.createElement('td'); td1.textContent = p.factor_b;
        const td2 = document.createElement('td'); td2.textContent = p.correlation.toFixed(3); td2.className = 'text-warn';
        tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2);
        corrBody.appendChild(tr);
      });
    } catch(e) { console.error(e); }
  }
  unmount() { this._unsubscribers.forEach(u => u()); }
}
export { EvalRenderer };
