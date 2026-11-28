#!/usr/bin/env node
/**
 * build_live_preview.mjs —— 生成「可直接打开 + 接真实数据」的 HTML 预览稿
 *
 * 特点：
 *   1. 内联真实 CSS（apple-tokens + apple-components + e-layout）⇒ 与真实前端同源；
 *   2. 内联 E 方案骨架 + 真实数据接入逻辑（AppleDeck 精简版）；
 *   3. 数据源：同源优先（直接打开时用相对路径 /api/...），
 *      file:// 下回退到可配置的本地后端地址（http://127.0.0.1:8000），
 *      配合后端 CORS（已加）跨源取数；
 *   4. API Key 可在页面上填写（存 localStorage），默认读取 sessionStorage。
 *
 * 用法：node scripts/build_live_preview.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

const TOKENS = read('static/css/apple-tokens.css');
const COMPONENTS = read('static/css/apple-components.css');
const ELAYOUT = read('static/css/e-layout.css');

// ── 内联脚本（真实数据接入）──
const APP_JS = `
const KEY_STORE = 'stock_scanner_api_key';
const BASE_STORE = 'stock_scanner_api_base';

function el(t, c, x) { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; }
function normCode(c) { return String(c || '').replace(/^(sh|sz|bj)/i, '').replace(/\\D/g, '').padStart(6, '0'); }

// 后端地址：file:// 打开时需要显式指定；由后端提供页面时留空走同源
function apiBase() {
  const saved = localStorage.getItem(BASE_STORE);
  if (location.protocol === 'file:') return saved || 'http://127.0.0.1:8017';
  return saved || '';
}
function apiKey() {
  return sessionStorage.getItem('api_key') || localStorage.getItem(KEY_STORE) || '';
}
async function api(pathname) {
  const url = apiBase() + pathname;
  const res = await fetch(url, { headers: { 'X-API-Key': apiKey() } });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch (e) {
    // 扫描进行中时后端可能返回非 JSON —— 归一为空结果，不当作致命错误
    return null;
  }
}

const SYM = {
  portfolio: '<path d="M3 3h6v8H3zM11 3h6v5h-6zM11 10h6v7h-6zM3 13h6v4H3z"/>',
  scanner:   '<path d="M3 12.5l3.5-4 2.8 2.4L14 5.5"/><path d="M14 5.5h-3.2M14 5.5v3.2"/>',
  risk:      '<path d="M10 2.5l6.5 2.6v4.4c0 3.5-2.7 6.2-6.5 7.6-3.8-1.4-6.5-4.1-6.5-7.6V5.1z"/>',
  positions: '<rect x="2.5" y="4" width="15" height="10" rx="2"/><path d="M2.5 8h15M7 4v10"/>',
  exposures: '<circle cx="10" cy="10" r="6.5"/><circle cx="10" cy="10" r="2.5"/>',
  signals:   '<circle cx="10" cy="10" r="2.2"/><path d="M4.8 4.8a7.4 7.4 0 000 10.4M15.2 4.8a7.4 7.4 0 010 10.4"/>',
  trades:    '<path d="M3 12l3.6-4 2.8 2.4L13.6 5.6"/><path d="M3 16h14"/>',
  backtest:  '<circle cx="10" cy="10" r="6.8"/><path d="M10 5.8V10l2.8 2"/>',
  strategy:  '<path d="M4.5 2.8v14.4M15.5 2.8v14.4M4.5 7h11M4.5 13h11"/>',
  audit:     '<path d="M5 2.8h10v14.4H5z"/><path d="M7.6 6.2h4.8M7.6 9h4.8M7.6 11.8h3"/>',
  reports:   '<rect x="3" y="2.8" width="14" height="14.4" rx="2"/><path d="M6 6.4h8M6 9.4h8M6 12.4h5"/>',
  notifications: '<path d="M10 3a4.2 4.2 0 00-4.2 4.2V10l-1.6 2.6h11.6L14.2 10V7.2A4.2 4.2 0 0010 3z"/><path d="M8.4 15.2a1.9 1.9 0 003.2 0"/>',
  settings:  '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.4v2.2M10 15.4v2.2M17.6 10h-2.2M4.6 10h-2.2M15.4 4.6l-1.6 1.6M6.2 13.8l-1.6 1.6M15.4 15.4l-1.6-1.6M6.2 6.2L4.6 4.6"/>'
};
const THEME_SVG = {
  light: '<circle cx="10" cy="10" r="3.8"/>',
  dark: '<path d="M15.8 11.2A6.6 6.6 0 019.4 4.8a6.6 6.6 0 106.4 6.4z"/>',
  system: '<rect x="2.4" y="3.6" width="15.2" height="10.6" rx="2"/><path d="M7.4 17.4h5.2"/>'
};
const NAV = [
  { g: '扫描', items: [ {id:'scanner',n:'选股结果'},{id:'signals',n:'信号追踪'} ] },
  { g: '组合', items: [ {id:'portfolio',n:'优化组合'},{id:'positions',n:'持仓概况'},{id:'exposures',n:'因子暴露'} ] },
  { g: '风险', items: [ {id:'risk',n:'风控中心'},{id:'backtest',n:'策略回测'} ] },
  { g: '记录', items: [ {id:'trades',n:'交易历史'},{id:'audit',n:'决策审计'},{id:'reports',n:'历史报告'} ] },
  { g: '', items: [ {id:'strategy',n:'策略对比'},{id:'notifications',n:'通知中心'},{id:'settings',n:'设置'} ] }
];

let watch = [];
let activeCode = null;

function renderSidebar() {
  const sb = document.getElementById('sidebar');
  sb.innerHTML = '';
  NAV.forEach(g => {
    if (g.g) sb.appendChild(el('div','sb-head',g.g));
    g.items.forEach(it => {
      const d = el('div','sb-item');
      d.dataset.panel = it.id;
      const ic = document.createElementNS('http://www.w3.org/2000/svg','svg');
      ic.setAttribute('viewBox','0 0 20 20'); ic.innerHTML = SYM[it.id] || '';
      d.appendChild(ic);
      d.appendChild(el('span','sb-label',it.n));
      sb.appendChild(d);
    });
  });
}

function renderHero() {
  const stats = document.getElementById('heroStats');
  stats.innerHTML = '';
  [['sentiment','市场情绪'],['macro','宏观门控'],['exposure','建议仓位'],
   ['sharpe','组合夏普'],['candidates','候选股'],['positions','持仓']].forEach(([id,label]) => {
    const d = el('div','hero-stat');
    d.appendChild(el('div','k',label));
    const v = el('div','v','--'); v.id = 'hero-' + id;
    d.appendChild(v);
    stats.appendChild(d);
  });
}

function setStat(k, v, tone) {
  const e = document.getElementById('hero-' + k);
  if (!e) return;
  e.textContent = v;
  e.classList.remove('up','down');
  if (tone) e.classList.add(tone);
}

function renderGrid() {
  const body = document.getElementById('gridBody');
  body.innerHTML = '';
  if (!watch.length) {
    const e = el('div','tile empty');
    e.appendChild(el('div','t','等待数据'));
    e.appendChild(el('div','c','后端未连接或扫描未完成'));
    body.appendChild(e);
    return;
  }
  watch.slice(0,12).forEach(t => {
    const tile = el('div','tile');
    if (t.code === activeCode) tile.classList.add('on');
    tile.appendChild(el('div','t',t.name));
    tile.appendChild(el('div','c', t.code ? (t.code + (t.sector ? ' · ' + t.sector : '')) : t.sector));
    const price = el('div','price');
    if (t.price != null) {
      price.appendChild(el('span','p', Number(t.price).toFixed(2)));
    } else if (t.signal) {
      const sigTxt = { bullish:'看多', bearish:'看空', neutral:'中性' }[t.signal] || t.signal;
      price.appendChild(el('span','p sig ' + (t.signal==='bullish'?'up':t.signal==='bearish'?'down':''), sigTxt));
    } else {
      price.appendChild(el('span','p','--'));
    }
    if (t.chg != null) {
      const c = Number(t.chg);
      price.appendChild(el('span','d ' + (c>=0?'up':'down'), (c>=0?'+':'') + c.toFixed(2) + '%'));
    }
    tile.appendChild(price);
    const sp = el('div','spark');
    const base = t.chg != null ? Number(t.chg) : 0;
    for (let i=0;i<12;i++){
      const h = 34 + base*(i/12)*2.2 + Math.sin(i*1.9+base)*9 + (i/12)*14;
      const bar = el('i', base>=0?'u':'d');
      bar.style.height = Math.max(8, Math.min(100,h)) + '%';
      bar.style.opacity = 0.28 + 0.5*(i/11);
      sp.appendChild(bar);
    }
    tile.appendChild(sp);
    tile.addEventListener('click', () => { activeCode = t.code; renderGrid(); renderInspector(t); });
    body.appendChild(tile);
  });
}

async function renderInspector(t) {
  const n = document.getElementById('insName'), c = document.getElementById('insCode');
  if (n) n.textContent = t.name;
  if (c) c.textContent = t.code ? t.code : (t.sector || '');
  const fb = document.getElementById('factorBreakdown');
  if (!fb) return;
  fb.innerHTML = '';
  if (!t.code) { fb.appendChild(el('div','ins-factor','板块策略无个股因子')); return; }
  try {
    const d = await api('/api/stock/' + t.code);
    const f = (d && (d.factors || d.factor_breakdown)) || null;
    if (Array.isArray(f) && f.length) {
      f.slice(0,8).forEach(x => {
        const key = x.name || x.factor || x.key || '';
        const val = x.value ?? x.score ?? x.weight ?? 0;
        const w = el('div','ins-factor');
        const fr = el('div','fr');
        fr.appendChild(el('span',null,key));
        fr.appendChild(el('span','fv', typeof val==='number' ? val.toFixed(1) : String(val)));
        w.appendChild(fr);
        const bar = el('div','ins-bar'); const i = el('i');
        i.style.width = Math.max(0, Math.min(100, Number(val)||0)) + '%';
        bar.appendChild(i); w.appendChild(bar);
        fb.appendChild(w);
      });
    } else { fb.appendChild(el('div','ins-factor','暂无因子明细')); }
  } catch(e) { fb.appendChild(el('div','ins-factor','因子数据暂不可用')); }
}

async function loadAll() {
  // 市场
  try {
    const m = await api('/api/market');
    if (m) {
      setStat('sentiment', m.sentiment != null ? Number(m.sentiment).toFixed(2) : '--',
        m.sentiment > 0.2 ? 'up' : m.sentiment < -0.2 ? 'down' : '');
      const map = { ALLOW_FULL:'通过', REDUCE_RISK:'减仓', BLOCK_NEW_RISK:'拦截' };
      setStat('macro', map[m.macro_gate?.state] || m.macro_gate?.state || '--',
        m.macro_gate?.state === 'BLOCK_NEW_RISK' ? 'down' : m.macro_gate?.state === 'REDUCE_RISK' ? '' : 'up');
      setStat('exposure', m.exposure?.exposure != null ? Math.round(m.exposure.exposure*100)+'%' : '--');
      setStat('sharpe', m.sharpe_ratio != null ? Number(m.sharpe_ratio).toFixed(2) : '--');
      setStat('positions', String(m.n_positions || 0));
      setStat('candidates', String(m.total_candidates || 0));
      document.getElementById('insSentiment').textContent = m.sentiment != null ? Number(m.sentiment).toFixed(2) : '--';
      document.getElementById('insMacro').textContent = map[m.macro_gate?.state] || '--';
      document.getElementById('insMacroReason').textContent = m.macro_gate?.reasoning || '';
      document.getElementById('insExposure').textContent = m.exposure?.exposure != null ? Math.round(m.exposure.exposure*100)+'%' : '--';
    }
  } catch(e) {
    setStatus(/Failed to fetch|NetworkError/.test(e.message) ? ('后端未连接 — 请检查地址与后端是否启动') : ('读取失败: ' + e.message), true);
  }

  // 候选
  try {
    const s = await api('/api/scan');
    const res = s && s.results ? s.results : {};
    const rows = [];
    Object.keys(res).forEach(k => {
      if (k === '热门板块') return;
      (res[k]||[]).forEach(it => {
        const code = normCode(it['代码']||it.code);
        if (!code || code==='000000') return;
        rows.push({ code, name: it['名称']||it.name||code, sector: it['策略']||k,
          price: it['最新价'] ?? it['现价'] ?? null,
          chg: it['涨跌幅'] ?? null,
          score: it['综合评分'] ?? null, signal: it['信号']||'' });
      });
    });
    if (rows.length) watch = rows;
    else if (Array.isArray(res['热门板块'])) {
      watch = res['热门板块'].map(x => ({ code:'', name:x['板块'], sector:'板块 · 涨停 '+(x['涨停数']??'--')+' 家',
        price: x['平均封板资金'] ?? null, chg: x['5日涨幅'] ?? null, score: x['sector_score'] ?? null }));
    }
    setStat('candidates', String(watch.length));
    renderGrid();
  } catch(e) {
    // 扫描未就绪 / 返回非 JSON —— 静默，保留已有数据
    renderGrid();
  }
}

function setStatus(text, err) {
  const el2 = document.getElementById('statusText');
  if (el2) { el2.textContent = text; el2.style.color = err ? 'var(--down)' : 'var(--label-2)'; }
}

function applyTheme(t) {
  localStorage.setItem('deckTheme', t);
  document.documentElement.setAttribute('data-theme', t);
  const b = document.getElementById('themeBtn');
  b.innerHTML = '<svg viewBox="0 0 20 20">' + THEME_SVG[t] + '</svg>';
  b.title = t==='dark'?'深色':(t==='light'?'浅色':'跟随系统');
}

document.addEventListener('DOMContentLoaded', () => {
  renderSidebar();
  renderHero();
  renderGrid();
  applyTheme(localStorage.getItem('deckTheme') || 'system');
  document.getElementById('themeBtn').addEventListener('click', () => {
    const ids = ['dark','light','system'];
    const cur = localStorage.getItem('deckTheme') || 'system';
    applyTheme(ids[(ids.indexOf(cur)+1)%ids.length]);
  });
  document.getElementById('csBtn').addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-color-scheme');
    const next = cur === 'china' ? 'international' : 'china';
    document.documentElement.setAttribute('data-color-scheme', next);
    localStorage.setItem('deckCs', next);
    document.getElementById('csBtn').textContent = next === 'china' ? '红涨绿跌' : '绿涨红跌';
  });
  if (localStorage.getItem('deckCs')) document.documentElement.setAttribute('data-color-scheme', localStorage.getItem('deckCs'));
  document.getElementById('csBtn').textContent = document.documentElement.getAttribute('data-color-scheme') === 'international' ? '绿涨红跌' : '红涨绿跌';

  // 连接设置
  const baseIn = document.getElementById('baseInput');
  const keyIn = document.getElementById('keyInput');
  baseIn.value = localStorage.getItem(BASE_STORE) || (location.protocol === 'file:' ? 'http://127.0.0.1:8017' : '');
  keyIn.value = localStorage.getItem(KEY_STORE) || '';
  document.getElementById('connBtn').addEventListener('click', () => {
    localStorage.setItem(BASE_STORE, baseIn.value.trim());
    localStorage.setItem(KEY_STORE, keyIn.value.trim());
    sessionStorage.setItem('api_key', keyIn.value.trim());
    loadAll();
  });
  loadAll();
  setInterval(loadAll, 60000);
});
`;

const BODY = `
<div class="app-layout">
  <aside class="sidebar" id="sidebar"></aside>
  <div class="main-area">
    <header class="toolbar">
      <div class="toolbar-group toolbar-brand-group">
        <div class="toolbar-brand">Quant A-Share</div>
        <div class="toolbar-breadcrumb">盘后复盘</div>
      </div>
      <div class="toolbar-spacer"></div>
      <button class="btn icon" id="themeBtn" title="主题"></button>
      <button class="btn" id="csBtn">红涨绿跌</button>
    </header>
    <div class="content-area">
      <div class="chart-area">
        <div class="hero">
          <h1>盘后复盘</h1>
          <div class="sub" id="heroSub">加载中…</div>
          <div class="hero-row" id="heroStats"></div>
        </div>
        <div class="grid">
          <div class="grid-head"><h2>观察列表</h2><span class="sub">候选与持仓</span></div>
          <div class="grid-body" id="gridBody"></div>
        </div>
      </div>
      <aside class="inspector">
        <div class="ins-block">
          <div class="ins-k">当前标的</div>
          <div class="ins-name" id="insName">全市场扫描</div>
          <div class="ins-code" id="insCode">A股 5,600+ 只</div>
        </div>
        <div class="ins-block">
          <div class="ins-k">核心读数</div>
          <div class="ins-row"><span class="k">情绪</span><span class="v" id="insSentiment">--</span></div>
          <div class="ins-row"><span class="k">门控</span><span class="v" id="insMacro">--</span></div>
          <div class="ins-row"><span class="k">依据</span><span class="v" id="insMacroReason">--</span></div>
          <div class="ins-row"><span class="k">仓位</span><span class="v" id="insExposure">--</span></div>
        </div>
        <div class="ins-block">
          <div class="ins-k">评分构成</div>
          <div id="factorBreakdown"></div>
        </div>
        <div class="ins-block">
          <div class="ins-k">连接设置</div>
          <div style="display:flex;flex-direction:column;gap:6px">
            <input id="baseInput" placeholder="后端地址（如 http://127.0.0.1:8000）" style="width:100%;height:26px;padding:0 8px;border:1px solid var(--border);background:var(--surface);color:var(--label);border-radius:var(--radius-xs);font-size:12px">
            <input id="keyInput" placeholder="API Key" style="width:100%;height:26px;padding:0 8px;border:1px solid var(--border);background:var(--surface);color:var(--label);border-radius:var(--radius-xs);font-size:12px">
            <button class="btn primary" id="connBtn" style="width:100%;justify-content:center">连接并刷新</button>
          </div>
        </div>
      </aside>
    </div>
    <footer class="statusbar">
      <span><i class="dot ok"></i>WebSocket</span>
      <span>数据源 TDX</span>
      <span id="statusText">正在连接…</span>
      <span class="status-spacer"></span>
      <span>沪深300: --</span>
    </footer>
  </div>
</div>
`;

// 内联脚本会被后端 CSP（script-src 'self'）拦截 —— 拆成同源外链
const outDir = path.join(ROOT, 'docs', 'preview');
fs.mkdirSync(outDir, { recursive: true });

const html = [
'<!DOCTYPE html>',
'<html lang="zh-CN" data-theme="dark">',
'<head>',
'<meta charset="UTF-8">',
'<meta name="viewport" content="width=device-width, initial-scale=1.0">',
'<title>Quant A-Share — 盘后复盘（E · Apple Stocks 式，接真实数据）</title>',
'<style>',
TOKENS,
COMPONENTS,
ELAYOUT,
'</style>',
'</head>',
'<body>',
BODY,
'<script src="live-apple-stocks.js"></script>',
'</body>',
'</html>'
].join('\n');

const outFile = path.join(outDir, 'live-apple-stocks.html');
fs.writeFileSync(outFile, html, 'utf8');
fs.writeFileSync(path.join(outDir, 'live-apple-stocks.js'), APP_JS, 'utf8');
console.log('[live-preview] ' + path.relative(ROOT, outFile) + '  (' + (html.length / 1024).toFixed(1) + ' KB + 同源 JS)');
