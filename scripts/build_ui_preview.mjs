#!/usr/bin/env node
/**
 * build_ui_preview.mjs —— 生成自包含的 UI 预览稿（可直接双击打开）
 *
 * 目的：让界面结构（TradingView 式重排）无需起服务即可查看与评审。
 * 做法：**从真实 CSS 文件内联生成**，不复制粘贴样式 ⇒ 预览稿与实现永不脱节。
 * 输出：docs/preview/tradingview-layout.html（单文件，含 CSS + JS + 假数据）
 *
 * 用法：
 *   node scripts/build_ui_preview.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => fs.readFileSync(path.join(ROOT, p), 'utf8');

// 1) 内联真实样式（单一真源：tokens.css）
const tokens = read('static/css/tokens.css');
const layout = read('static/css/layout.css');
const main = read('static/css/main.css');

// 构建期用的面板清单（供模板生成；浏览器端另有一份交互用的同名常量）
const BUILD_PANELS = [
  { key: 'risk', label: '风控中心' },
  { key: 'positions', label: '持仓概况' },
  { key: 'signals', label: '信号追踪' },
  { key: 'backtest', label: '策略回测' },
  { key: 'strategy', label: '策略对比' },
  { key: 'settings', label: '设置' },
];

// 构建期：生成内联 SVG 柱状图（不依赖 CSS 高度计算，最稳）
function barSvg(values) {
  const W = 600, H = 200, pad = 14;
  const bw = (W - pad * 2) / values.length;
  const bars = values.map((v, i) => {
    const h = (v / 100) * (H - pad * 2);
    const x = pad + i * bw + bw * 0.15;
    const w = bw * 0.7;
    // 单色阶梯（评分不是涨跌，不该用红绿）
    const fill = 'var(--accent)';
    const op = (0.35 + 0.55 * (v / 100)).toFixed(2);
    return '<rect x="' + x.toFixed(1) + '" y="' + (H - pad - h).toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + h.toFixed(1) + '" fill="' + fill + '" opacity="' + op + '" rx="1.5"/>';
  }).join('');
  const grid = [0.25, 0.5, 0.75].map(f => {
    const y = ((H - pad) - f * (H - pad * 2)).toFixed(1);
    return '<line x1="' + pad + '" y1="' + y + '" x2="' + (W - pad) + '" y2="' + y + '" stroke="var(--border)" stroke-width="1"/>';
  }).join('');
  return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none" style="width:100%;height:100%;display:block">' + grid + bars + '</svg>';
}

// 2) 预览稿用到的少量交互脚本（原生，无依赖）
const script = `
const ICON_SVG = {
  portfolio: '<path d="M2 12.5h3.2V6.5H2zM7.4 12.5h3.2V2.5H7.4zM12.8 12.5H16V8h-3.2z"/>',
  scanner:   '<path d="M2 13.5l3.6-4.2 3 2.6L13.4 3.5 16 5.2"/>',
  risk:      '<path d="M9 2.2l6.2 2.4v4.2c0 3.2-2.5 5.6-6.2 6.9-3.7-1.3-6.2-3.7-6.2-6.9V4.6z"/>',
  positions: '<rect x="2.2" y="4.6" width="13.6" height="9.2" rx="1"/><path d="M2.2 8h13.6M6.6 4.6v9.2"/>',
  exposures: '<circle cx="9" cy="9" r="6.3"/><circle cx="9" cy="9" r="2.4"/>',
  signals:   '<circle cx="9" cy="9" r="2.1"/><path d="M4.2 4.2a6.8 6.8 0 000 9.6M13.8 4.2a6.8 6.8 0 010 9.6"/>',
  trades:    '<path d="M2.6 11.4l3.4-3.6 2.6 2.4 4-4.6 2.8 2.8"/><path d="M2.6 14.2h13"/>',
  backtest:  '<circle cx="9" cy="9" r="6.4"/><path d="M9 5.2V9l2.6 1.8"/>',
  strategy:  '<path d="M4 3.2v12.4M14 3.2v12.4M4 6.6h10M4 11.4h10"/>',
  audit:     '<path d="M4.4 2.4h9.2v13.2H4.4z"/><path d="M6.6 5.4h4.8M6.6 8h4.8M6.6 10.6h3"/>',
  reports:   '<rect x="2.6" y="2.8" width="12.8" height="12.4" rx="1"/><path d="M5.6 6.2h6.8M5.6 9h6.8M5.6 11.8h4"/>',
  notifications: '<path d="M9 2.6a4 4 0 00-4 4v2.8L3.6 12h10.8L13 9.4V6.6a4 4 0 00-4-4z"/><path d="M7.4 14.2a1.8 1.8 0 002.8 0"/>',
  settings:  '<circle cx="9" cy="9" r="2.4"/><path d="M9 2.4v2M9 13.6v2M15.6 9h-2M4.4 9h-2M13.7 4.3l-1.4 1.4M5.7 12.3l-1.4 1.4M13.7 13.7l-1.4-1.4M5.7 5.7L4.3 4.3"/>',
};
const PANELS = {
  portfolio: { label: '组合优化', panes: ['chart', 'data'] },
  scanner:   { label: '选股结果', panes: ['chart', 'data'] },
  risk:      { label: '风控中心', panes: ['chart', 'data'] },
  positions: { label: '持仓概况', panes: ['chart', 'data'] },
  signals:   { label: '信号追踪', panes: ['chart', 'data'] },
  backtest:  { label: '策略回测', panes: ['chart', 'data'] },
  strategy:  { label: '策略对比', panes: ['chart', 'data'] },
  settings:  { label: '设置',     panes: ['chart', 'data'] },
};
const NAV = Object.keys(PANELS);

const WATCH = [
  { code: '600519', name: '贵州茅台', price: 1682.50, chg: 1.24 },
  { code: '000858', name: '五粮液',   price: 142.30,  chg: -0.86 },
  { code: '300750', name: '宁德时代', price: 196.80,  chg: 2.07 },
  { code: '601318', name: '中国平安', price: 48.62,   chg: 0.35 },
  { code: '002415', name: '海康威视', price: 31.14,   chg: -1.12 },
  { code: '600036', name: '招商银行', price: 34.52,   chg: 0.58 },
];

const HOLDINGS = [
  ['603960','克来机电','机械设备','9.5%','0.2'],
  ['603039','全筑股份','建筑装饰','3.0%','0.1'],
  ['002651','利君股份','国防军工','2.5%','0.1'],
  ['002872','天圣制药','医药商业','2.5%','0.1'],
  ['600280','中央商场','商业贸易','2.5%','0.1'],
  ['000158','常山北明','软件开发','4.3%','1.3'],
  ['002261','拓维信息','计算机','9.5%','0.2'],
  ['000021','深科技','电子','6.3%','3.8'],
];

function renderWatchlist() {
  const box = document.getElementById('watchList');
  box.innerHTML = '';
  WATCH.forEach(w => {
    const row = document.createElement('div');
    row.className = 'watch-item';
    const up = w.chg >= 0;
    row.innerHTML =
      '<span class="watch-item-code">' + w.code + '</span>' +
      '<span class="watch-item-name">' + w.name + '</span>' +
      '<span class="watch-item-last">' + w.price.toFixed(2) + '</span>' +
      '<span class="watch-item-chg ' + (up ? 'up' : 'down') + '">' +
      (up ? '+' : '') + w.chg.toFixed(2) + '%</span>';
    row.addEventListener('click', () => {
      document.querySelectorAll('.watch-item').forEach(r => r.style.background = '');
      row.style.background = 'var(--bg-hover)';
      window.updateSymbolBar({
        ticker: w.name, name: w.code,
        change_pct: w.chg,
        meta: ['评分 78.5'],
      });
    });
    box.appendChild(row);
  });
}

function renderTable() {
  const body = document.getElementById('holdBody');
  body.innerHTML = '';
  HOLDINGS.forEach(h => {
    const tr = document.createElement('tr');
    h.forEach((v, i) => {
      const td = document.createElement('td');
      td.textContent = v;
      if (i === 3) td.className = 'text-accent';
      tr.appendChild(td);
    });
    tr.addEventListener('click', () => {
      window.updateSymbolBar({
        ticker: h[1], name: h[0], change_pct: null,
        meta: [h[2] + ' / 权重 ' + h[3]],
      });
    });
    body.appendChild(tr);
  });
}

function buildNav() {
  const sb = document.getElementById('sidebar');
  sb.innerHTML = '';
  NAV.forEach((k, i) => {
    const d = document.createElement('div');
    d.className = 'nav-item' + (i === 0 ? ' active' : '');
    d.dataset.panel = k;
    d.title = PANELS[k].label;
    d.innerHTML = '<span class="nav-icon"><svg viewBox="0 0 18 18" focusable="false">' + (ICON_SVG[k] || '') + '</svg></span>' +
                  '<span class="nav-label">' + PANELS[k].label + '</span>';
    d.addEventListener('click', () => {
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      d.classList.add('active');
      document.getElementById('crumb').textContent = PANELS[k].label;
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.getElementById('panel-' + k).classList.add('active');
    });
    sb.appendChild(d);
  });
}

function tickClock() {
  const d = new Date();
  const pad = n => String(n).padStart(2, '0');
  const el = document.getElementById('symbolClock');
  if (el) el.textContent = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}

window.updateSymbolBar = function (data) {
  if (!data) return;
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.textContent = v; };
  set('symbolTicker', data.ticker || '全市场扫描');
  set('symbolName', data.code || data.name || 'A股 5,600+ 只');
  const chg = data.change_pct != null ? Number(data.change_pct) : null;
  const chgEl = document.getElementById('symbolChange');
  if (chgEl) {
    chgEl.textContent = chg == null ? '--' : (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%';
    chgEl.className = 'tape-delta ' + (chg == null ? 'flat' : (chg >= 0 ? 'up' : 'down'));
  }
  const m = Array.isArray(data.meta) ? data.meta : [];
  set('symbolMeta3', m[0] || '--');
};

document.addEventListener('DOMContentLoaded', () => {
  buildNav();
  renderWatchlist();
  renderTable();
  tickClock();
  setInterval(tickClock, 1000);
  const themeBtn = document.getElementById('themeBtn');
  const THEME_SVG = {
    dark: '<path d="M14.2 10.4A6 6 0 018.6 4.8a6 6 0 105.6 5.6z"/>',
    light: '<circle cx="9" cy="9" r="3.6"/><path d="M9 1.8v1.8M9 14.4v1.8M16.2 9h-1.8M3.6 9H1.8M14.1 3.9l-1.3 1.3M5.2 12.8l-1.3 1.3M14.1 14.1l-1.3-1.3M5.2 5.2L3.9 3.9"/>',
    system: '<rect x="1.8" y="3" width="14.4" height="10" rx="1"/><path d="M6.6 16.2h4.8"/>',
  };
  let theme = localStorage.getItem('theme') || 'system';
  const applyTheme = (t) => {
    theme = t;
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('theme', t);
    themeBtn.innerHTML = '<svg viewBox="0 0 18 18" focusable="false">' + (THEME_SVG[t] || THEME_SVG.system) + '</svg>';
  };
  applyTheme(theme);
  themeBtn.addEventListener('click', () => {
    const order = ['dark', 'light', 'system'];
    applyTheme(order[(order.indexOf(theme) + 1) % order.length]);
  });
  // 涨跌配色两方案
  const csBtn = document.getElementById('csBtn');
  if (csBtn) {
    let cs = localStorage.getItem('colorScheme') || 'china';
    const applyCs = (v) => {
      cs = v;
      document.documentElement.setAttribute('data-color-scheme', v);
      localStorage.setItem('colorScheme', v);
      csBtn.textContent = v === 'china' ? '红涨绿跌' : '绿涨红跌';
    };
    applyCs(cs);
    csBtn.addEventListener('click', () => applyCs(cs === 'china' ? 'international' : 'china'));
  }
});
`;

// 把占位标记替换成真实 SVG（构建期求值）
const BARS = [
  [70,45,88,32,61,24,79,52],
  [22,35,48,41,66,58,74,63],
  [55,72,38,90,44,66,30,81],
  [44,68,35,82,51,73,60,47],
  [39,57,66,48,72,54,62,44],
];
let barsIdx = 0;
const htmlSrc = `

<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Quant A-Share — TradingView 式布局预览（自包含）</title>
<style>
/* ===== tokens.css（唯一真源，内联） ===== */
${tokens}
/* ===== layout.css（内联） ===== */
${layout}
/* ===== main.css（内联） ===== */
${main}

/* ===== 预览稿专用：图表占位（无外部依赖） ===== */
.chart-placeholder {
  height: 100%;
  min-height: 200px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background:
    repeating-linear-gradient(0deg, transparent, transparent 27px, var(--border) 27px, var(--border) 28px),
    repeating-linear-gradient(90deg, transparent, transparent 63px, var(--border) 63px, var(--border) 64px);
  position: relative;
  overflow: hidden;
}
.chart-placeholder::after {
  content: attr(data-name);
  position: absolute; left: 8px; top: 6px;
  font-size: 10px; color: var(--text-dim);
}
.bars { display: flex; align-items: flex-end; gap: 6px; flex: 1; height: 100%; padding: 16px 16px 8px; }
.bars i { display: block; }
.bars i { flex: 1 1 0; min-width: 8px; background: var(--accent); opacity: .75; border-radius: 2px 2px 0 0; align-self: flex-end; }
.bars i:nth-child(2n) { background: var(--up); opacity: .5; }
.bars i:nth-child(3n) { background: var(--down); opacity: .45; }
.preview-note {
  margin: 10px 12px 0; padding: 8px 12px;
  border: 1px dashed var(--border); border-radius: var(--radius-sm);
  font-size: 11px; color: var(--text-dim); line-height: 1.6;
}
.preview-note b { color: var(--text-secondary); }
${''}
</style>
</head>
<body>
<div class="app-layout">
  <aside class="sidebar" id="sidebar"></aside>
  <div class="main-area">
    <header class="toolbar">
      <div class="toolbar-group toolbar-brand-group">
        <div class="toolbar-brand">Quant A-Share</div>
        <div class="toolbar-breadcrumb" id="crumb">组合优化</div>
      </div>
      <div class="toolbar-separator"></div>
      <div class="toolbar-group tf-group">
        <button class="tf-btn active">日线</button>
        <button class="tf-btn">周线</button>
        <button class="tf-btn">月线</button>
      </div>
      <div class="toolbar-separator"></div>
      <div class="toolbar-group ind-group">
        <button class="ind-btn">MA</button>
        <button class="ind-btn">MACD</button>
        <button class="ind-btn">RSI</button>
      </div>
      <div class="toolbar-spacer"></div>
      <button class="theme-toggle" id="themeBtn" title="主题"><svg viewBox="0 0 18 18" focusable="false"><rect x="1.8" y="3" width="14.4" height="10" rx="1"/><path d="M6.6 16.2h4.8"/></svg></button>
    </header>

    <div class="tape">
      <div class="tape-cell tape-cell-symbol">
        <span class="tape-symbol-name" id="symbolTicker">全市场扫描</span>
        <span class="tape-symbol-code" id="symbolName">A股 5,600+ 只</span>
      </div>
      <div class="tape-cell"><span class="tape-label">候选</span><span class="tape-value">30</span></div>
      <div class="tape-cell"><span class="tape-label">情绪</span><span class="tape-value">0.94</span><span class="tape-sub">偏多</span></div>
      <div class="tape-cell"><span class="tape-label">门控</span><span class="tape-value">通过</span><span class="tape-sub">正常交易</span></div>
      <div class="tape-cell"><span class="tape-label">仓位</span><span class="tape-value">64%</span><span class="tape-sub">乘数 100%</span></div>
      <div class="tape-cell"><span class="tape-label">夏普</span><span class="tape-value">0.06</span><span class="tape-sub">预期</span></div>
      <div class="tape-cell"><span class="tape-label">持仓</span><span class="tape-value">16</span><span class="tape-sub">优化组合</span></div>
      <div class="tape-cell"><span class="tape-label">涨跌</span><span class="tape-delta flat" id="symbolChange">--</span><span class="tape-sub" id="symbolMeta3">--</span></div>
      <div class="tape-spacer"></div>
      <div class="tape-cell"><span class="tape-clock" id="symbolClock">--</span></div>
    </div>

    <div class="content-area">
      <div class="chart-area">
        <div class="panels-container">
          <!-- 组合优化 -->
          <div class="panel active" id="panel-portfolio">
            <div class="pane-split">
              <section class="pane pane-primary">
                <header class="pane-header"><span class="pane-title">图表</span></header>
                <div class="pane-body">
                  <div class="chart-placeholder" data-name="行业暴露">__BARSVG_1__</div>
                  <div class="chart-placeholder" data-name="组合预期收益 vs 波动率">__BARSVG_2__</div>
                </div>
              </section>
              <section class="pane pane-secondary">
                <header class="pane-header"><span class="pane-title">数据</span></header>
                <div class="pane-body">
                  <table><thead><tr><th>代码</th><th>名称</th><th>行业</th><th>权重</th><th>评分</th></tr></thead><tbody id="holdBody"></tbody></table>
                </div>
              </section>
            </div>
          </div>

          <!-- 选股结果 -->
          <div class="panel" id="panel-scanner">
            <div class="pane-split">
              <section class="pane pane-primary">
                <header class="pane-header"><span class="pane-title">图表</span></header>
                <div class="pane-body">
                  <div class="chart-placeholder" data-name="板块热力图（Top 30）">__BARSVG_3__</div>
                </div>
              </section>
              <section class="pane pane-secondary">
                <header class="pane-header"><span class="pane-title">数据</span></header>
                <div class="pane-body">
                  <table><thead><tr><th>板块</th><th>涨停数</th><th>最高连板</th><th>代表股</th><th>平均板块资金</th><th>5日涨幅</th></tr></thead>
                  <tbody>
                    <tr><td>半导体</td><td>64</td><td>2</td><td>三佳科技、金海通、华海诚科</td><td>61144188.28</td><td>15</td></tr>
                    <tr><td>IT服务 II</td><td>34</td><td>2</td><td>常山北明、浙大网新、大位科技</td><td>58950162.74</td><td>11.18</td></tr>
                    <tr><td>元件</td><td>21</td><td>2</td><td>骏亚科技、杰昌电子、兴森科技</td><td>1027608049.38</td><td>11.35</td></tr>
                    <tr><td>通信设备</td><td>21</td><td>2</td><td>楚天龙、鼎信通讯、意华股份</td><td>60073485.57</td><td>11.35</td></tr>
                    <tr><td>软件开发</td><td>16</td><td>2</td><td>天源迪科、拓维信息、法本信息</td><td>44248822.69</td><td>11.46</td></tr>
                  </tbody></table>
                </div>
              </section>
            </div>
          </div>

          ${BUILD_PANELS.map(p => `
          <div class="panel" id="panel-${p.key}">
            <div class="pane-split">
              <section class="pane pane-primary">
                <header class="pane-header"><span class="pane-title">图表</span></header>
                <div class="pane-body"><div class="chart-placeholder" data-name="${p.label}">__BARSVG_4__</div></div>
              </section>
              <section class="pane pane-secondary">
                <header class="pane-header"><span class="pane-title">数据</span></header>
                <div class="pane-body"><table><thead><tr><th>项目</th><th>数值</th><th>说明</th></tr></thead><tbody>
                  <tr><td>样本数</td><td>16</td><td>优化组合持仓</td></tr>
                  <tr><td>夏普</td><td>0.06</td><td>预期夏普比</td></tr>
                  <tr><td>行业数</td><td>0</td><td>行业暴露分布</td></tr>
                </tbody></table></div>
              </section>
            </div>
          </div>`).join('')}
        </div>
      </div>

      <aside class="right-panel" id="right-panel">
        <div class="panel-section">
          <div class="panel-title">快捷操作</div>
          <button class="refresh-btn">刷新数据</button>
          <select class="preset-select"><option>均衡策略</option><option>激进策略</option><option>保守策略</option></select>
        </div>
        <div class="panel-section">
          <div class="panel-title">观察列表</div>
          <div class="watch-head">
            <span class="watch-col-code">代码</span><span class="watch-col-name">名称</span>
            <span class="watch-col-last">最新</span><span class="watch-col-chg">涨跌</span>
          </div>
          <div id="watchList"></div>
        </div>
      </aside>
    </div>

    <footer class="statusbar">
      <div class="status-item"><span class="status-dot connected"></span><span>WebSocket 已连接</span></div>
      <div class="status-item">数据源: TDX</div>
      <div class="status-item">最后更新: --:--:--</div>
      <div class="status-spacer"></div>
      <div class="status-item">沪深300: 加载中...</div>
    </footer>
  </div>
</div>

<div class="preview-note">
  <b>自包含预览稿</b>（双击即可打开，无需起服务）——样式由 <b>static/css/tokens.css · layout.css · main.css</b> 内联生成，
  与真实实现同源；界面结构为 TradingView 式重排：顶部 <b>symbol bar</b>（标的/价格/涨跌/时钟）、
  紧凑 <b>ticker 条</b>、<b>上下窗格</b>（图表窗格 + 数据窗格）、右侧<b>行情表</b>式观察列表。
  点击左侧导航切换面板，点击观察列表或持仓行可更新 symbol bar，右上角切换主题。
  重新生成：<b>node scripts/build_ui_preview.mjs</b>
</div>

<script>
${script}
</script>
</body>
</html>
`;

const outDir = path.join(ROOT, 'docs', 'preview');
fs.mkdirSync(outDir, { recursive: true });
const outFile = path.join(outDir, 'tradingview-layout.html');
const html = htmlSrc.replace(/__BARSVG_(\d+)__/g, () => barSvg(BARS[barsIdx++ % BARS.length]));
fs.writeFileSync(outFile, html, 'utf8');
console.log('[preview] ' + path.relative(ROOT, outFile) + '  (' + (html.length / 1024).toFixed(1) + ' KB, 自包含)');
