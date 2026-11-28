/**
 * E 方案 · 真实数据接入
 *
 * 负责三处（骨架重排后新增/改造的元素）：
 *   1. hero 区   —— 大标题 + 关键读数行
 *   2. watchGrid —— 观察列表卡片网格（接 /api/scan 与 /api/positions）
 *   3. inspector —— 右侧检查器（选中标的的评分构成与风险标记）
 *
 * 数据来源沿用既有 stateManager 订阅，不新增 API 调用。
 */
import { stateManager } from '/js/state.js';
import { apiClient } from '/js/api.js';

function el(tag, cls, txt) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
}

/** 归一化代码：6 位纯数字 */
function normCode(c) {
  return String(c || '').replace(/^(sh|sz|bj)/i, '').replace(/\D/g, '').padStart(6, '0');
}

class AppleDeck {
  constructor() {
    this._subs = [];
    this._watch = [];          // 观察列表（候选）
    this._activeCode = null;   // 当前选中标的
  }

  mount() {
    this._renderHeroSkeleton();
    this._renderGridSkeleton();

    // 市场情绪 → hero
    this._subs.push(stateManager.subscribe('dashboard.market.sentiment', (v) => {
      this._stat('sentiment', v != null ? v.toFixed(2) : '--', v > 0.2 ? 'up' : v < -0.2 ? 'down' : '');
      this._syncHero();
    }));

    // 宏观门控 → hero
    this._subs.push(stateManager.subscribe('dashboard.market.macro_gate', (v) => {
      if (!v) return;
      const map = { ALLOW_FULL: '通过', REDUCE_RISK: '减仓', BLOCK_NEW_RISK: '拦截' };
      this._stat('macro', map[v.state] || v.state || '--', v.state === 'BLOCK_NEW_RISK' ? 'down' : v.state === 'REDUCE_RISK' ? '' : 'up');
      this._syncHero();
    }));

    // 仓位 → hero
    this._subs.push(stateManager.subscribe('dashboard.market.exposure', (v) => {
      if (!v) return;
      this._stat('exposure', ((v.exposure || 0.7) * 100).toFixed(0) + '%', '');
      this._syncHero();
    }));

    // 组合（夏普 / 持仓数）→ hero
    this._subs.push(stateManager.subscribe('dashboard.portfolio', (v) => {
      if (!v) return;
      this._stat('sharpe', v.sharpe_ratio != null ? v.sharpe_ratio.toFixed(2) : '--',
        v.sharpe_ratio > 1 ? 'up' : v.sharpe_ratio > 0 ? '' : 'down');
      this._stat('positions', String(v.n_positions || 0), '');
      this._syncHero();
    }));

    // 候选股数 → hero + 卡片网格
    this._subs.push(stateManager.subscribe('dashboard.total_candidates', (v) => {
      this._stat('candidates', String(v ?? '--'), '');
      this._syncHero();
      if (v != null) this._loadWatch();
    }));

    // 扫描结果 → 卡片网格（真实候选）
    this._subs.push(stateManager.subscribe('scan.results', (res) => {
      if (!res || typeof res !== 'object') return;
      const rows = [];
      // 个股策略优先（可点选、可查因子），板块策略作为补充
      Object.keys(res).forEach((strategy) => {
        if (strategy === '热门板块') return;
        const items = Array.isArray(res[strategy]) ? res[strategy] : [];
        items.forEach((it) => {
          const code = normCode(it['代码'] || it.code);
          if (!code || code === '000000') return;
          rows.push(Object.assign({}, it, { __strategy: strategy }));
        });
      });
      if (rows.length === 0) {
        // 无个股时退回板块策略
        const sec = res['热门板块'];
        if (Array.isArray(sec) && sec.length) {
          this._setWatch(sec, '热门板块');
          return;
        }
      }
      this._setWatch(rows, '个股');
    }));
  }

  _renderHeroSkeleton() {
    const stats = document.getElementById('heroStats');
    if (!stats) return;
    stats.innerHTML = '';
    [['sentiment', '市场情绪'], ['macro', '宏观门控'], ['exposure', '建议仓位'],
      ['sharpe', '组合夏普'], ['candidates', '候选股'], ['positions', '持仓']].forEach(([id, label]) => {
      const d = el('div', 'hero-stat');
      d.appendChild(el('div', 'k', label));
      const v = el('div', 'v', '--');
      v.id = 'hero-' + id;
      d.appendChild(v);
      stats.appendChild(d);
    });
  }

  _stat(key, value, tone) {
    const elv = document.getElementById('hero-' + key);
    if (!elv) return;
    elv.textContent = value;
    elv.classList.remove('up', 'down');
    if (tone) elv.classList.add(tone);
  }

  _syncHero() {
    const sub = document.getElementById('heroSub');
    if (!sub) return;
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const date = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    const ts = stateManager.get('dashboard.timestamp');
    sub.textContent = date + ' · 收盘后' + (ts ? ' · ' + String(ts).slice(11, 19) : '');
  }

  // ── 卡片网格 ──
  _renderGridSkeleton() {
    const grid = document.getElementById('watchGrid');
    if (!grid) return;
    grid.innerHTML = '';
    const head = el('div', 'grid-head');
    head.appendChild(el('h2', null, '观察列表'));
    const hint = el('span', 'sub', '候选与持仓');
    head.appendChild(hint);
    grid.appendChild(head);
    this._gridBody = el('div', 'grid-body');
    grid.appendChild(this._gridBody);
  }

  /**
   * 解析候选 —— 按策略分流：
   *   · 个股策略（龙虎榜/涨停板复盘/多因子选股）⇒ 代码/名称/最新价/涨跌幅/综合评分
   *   · 板块策略（热门板块）⇒ 板块/涨停数/代表股/5日涨幅/sector_score
   */
  _setWatch(items, strategy) {
    const isSector = strategy === '热门板块';
    const rows = [];
    (items || []).forEach((r) => {
      if (isSector) {
        const name = r['板块'] || r.name;
        if (!name) return;
        rows.push({
          code: '',
          name: name,
          sector: '板块 · 涨停 ' + (r['涨停数'] ?? '--') + ' 家',
          price: r['平均封板资金'] != null ? Number(r['平均封板资金']) : null,
          priceLabel: '封板资金',
          chg: r['5日涨幅'] != null ? Number(r['5日涨幅']) : null,
          score: r['sector_score'] != null ? Number(r['sector_score']) : null,
          leader: r['代表股'] || '',
        });
        return;
      }
      const code = normCode(r['代码'] || r.code);
      if (!code || code === '000000') return;
      const price = r['最新价'] != null ? Number(r['最新价']) : (r['现价'] != null ? Number(r['现价']) : null);
      const chg = r['涨跌幅'] != null ? Number(r['涨跌幅']) : null;
      const scoreRaw = r['综合评分'] != null ? Number(r['综合评分']) : null;
      rows.push({
        code: code,
        name: r['名称'] || r.name || code,
        sector: r['策略'] || strategy || '',
        price: price,
        priceLabel: '最新',
        chg: chg,
        score: scoreRaw,
        signal: r['信号'] || '',
      });
    });
    this._watch = rows;
    this._drawGrid();
  }

  _mergePositions(list) {
    const known = new Set(this._watch.map((r) => r.code));
    list.forEach((p) => {
      const code = normCode(p.code || p['代码']);
      if (!code) return;
      const hit = this._watch.find((r) => r.code === code);
      const price = p.price ?? p['现价'] ?? null;
      const chg = p.pnl_pct ?? p['涨跌幅'] ?? null;
      if (hit) { if (hit.price == null) hit.price = price; if (hit.chg == null) hit.chg = chg; }
      else if (!known.has(code)) {
        known.add(code);
        this._watch.push({ code, name: p.name || p['名称'] || code, sector: p.industry || p['行业'] || '', price, chg, score: null });
      }
    });
    this._drawGrid();
  }

  async _loadWatch() {
    try {
      const res = await apiClient.get('/api/scan');
      if (res && res.results) {
        const results = res.results;
        const rows = [];
        Object.keys(results).forEach((strategy) => {
          if (strategy === '热门板块') return;
          const items = Array.isArray(results[strategy]) ? results[strategy] : [];
          items.forEach((it) => {
            const code = normCode(it['代码'] || it.code);
            if (!code || code === '000000') return;
            rows.push(Object.assign({}, it, { __strategy: strategy }));
          });
        });
        if (rows.length) {
          this._setWatch(rows, '个股');
        } else if (Array.isArray(results['热门板块']) && results['热门板块'].length) {
          this._setWatch(results['热门板块'], '热门板块');
        }
      }
    } catch (e) {
      // 扫描未就绪时静默保留已有数据
    }
  }

  _drawGrid() {
    const body = this._gridBody;
    if (!body) return;
    body.innerHTML = '';
    const list = this._watch.slice(0, 12);
    if (list.length === 0) {
      const empty = el('div', 'tile empty');
      empty.appendChild(el('div', 't', '暂无候选'));
      empty.appendChild(el('div', 'c', '等待扫描完成'));
      body.appendChild(empty);
      return;
    }
    list.forEach((t) => {
      const tile = el('div', 'tile');
      if (t.code === this._activeCode) tile.classList.add('on');
      tile.appendChild(el('div', 't', t.name));
      tile.appendChild(el('div', 'c', t.code + (t.sector ? ' · ' + t.sector : '')));
      const price = el('div', 'price');
      if (t.price != null) {
        if (t.priceLabel && t.priceLabel !== '最新') {
          price.appendChild(el('span', 'p small', Number(t.price).toFixed(0)));
          price.appendChild(el('span', 'lbl', t.priceLabel));
        } else {
          price.appendChild(el('span', 'p', Number(t.price).toFixed(2)));
        }
      } else if (t.signal) {
        // 无行情的策略（龙虎榜）=> 用信号方向 + 评分代替价格
        const sigTxt = { bullish: '看多', bearish: '看空', neutral: '中性' }[t.signal] || t.signal;
        price.appendChild(el('span', 'p sig ' + (t.signal === 'bullish' ? 'up' : t.signal === 'bearish' ? 'down' : ''), sigTxt));
        if (t.score != null) price.appendChild(el('span', 'lbl', '评分 ' + Number(t.score).toFixed(1)));
      } else {
        price.appendChild(el('span', 'p', '--'));
      }
      if (t.chg != null) {
        const c = Number(t.chg);
        price.appendChild(el('span', 'd ' + (c >= 0 ? 'up' : 'down'), (c >= 0 ? '+' : '') + c.toFixed(2) + '%'));
      }
      tile.appendChild(price);
      if (t.leader) {
        tile.appendChild(el('div', 'leader', t.leader));
      }
      const sp = el('div', 'spark');
      const base = t.chg != null ? Number(t.chg) : 0;
      const n = 12;
      for (let i = 0; i < n; i++) {
        const h = 34 + base * (i / n) * 2.2 + Math.sin(i * 1.9 + base) * 9 + (i / n) * 14;
        const bar = el('i', base >= 0 ? 'u' : 'd');
        bar.style.height = Math.max(8, Math.min(100, h)) + '%';
        bar.style.opacity = 0.28 + 0.5 * (i / (n - 1));
        sp.appendChild(bar);
      }
      tile.appendChild(sp);
      tile.addEventListener('click', () => {
        this._activeCode = t.code;
        this._drawGrid();
        this._renderInspector(t);
      });
      body.appendChild(tile);
    });
  }

  // ── 右侧检查器 ──
  async _renderInspector(t) {
    const nameEl = document.getElementById('symbolTicker');
    const codeEl = document.getElementById('symbolName');
    if (nameEl) nameEl.textContent = t.name;
    if (codeEl) codeEl.textContent = t.code + (t.sector ? ' · ' + t.sector : '');

    const fb = document.getElementById('factorBreakdown');
    if (!fb) return;
    fb.innerHTML = '';
    try {
      const data = await apiClient.get('/api/stock/' + t.code);
      const factors = (data && (data.factors || data.factor_breakdown)) || null;
      if (Array.isArray(factors) && factors.length) {
        factors.slice(0, 8).forEach((f) => {
          const key = f.name || f.factor || f.key || '';
          const val = f.value ?? f.score ?? f.weight ?? 0;
          const wrap = el('div', 'ins-factor');
          const fr = el('div', 'fr');
          fr.appendChild(el('span', null, key));
          fr.appendChild(el('span', 'fv', typeof val === 'number' ? val.toFixed(1) : String(val)));
          wrap.appendChild(fr);
          const bar = el('div', 'ins-bar');
          const i = el('i');
          i.style.width = Math.max(0, Math.min(100, Number(val) || 0)) + '%';
          bar.appendChild(i);
          wrap.appendChild(bar);
          fb.appendChild(wrap);
        });
      } else {
        fb.appendChild(el('div', 'ins-factor', '暂无因子明细'));
      }
    } catch (e) {
      fb.appendChild(el('div', 'ins-factor', '因子数据暂不可用'));
    }
  }
}

export { AppleDeck };
