import { wsManager } from '/js/websocket.js';
import { stateManager } from '/js/state.js';
import { apiClient } from '/js/api.js';
import { pollingController } from '/js/polling.js';
import { getThemeColor } from '/js/theme.js';
import { initNotifications } from '/js/notifications.js';
import { initErrorTracker } from '/js/error_tracker.js';
import { Onboarding } from '/js/onboarding.js';
import { MetricsRenderer } from '/js/renderers/metrics.js';
import { PortfolioRenderer } from '/js/renderers/portfolio.js';
import { ScannerRenderer } from '/js/renderers/scanner.js';
import { RiskRenderer } from '/js/renderers/risk.js';
import { PositionsRenderer } from '/js/renderers/positions.js';
import { ExposuresRenderer } from '/js/renderers/exposures.js';
import { AuditRenderer } from '/js/renderers/audit.js';
import { BacktestRenderer } from '/js/renderers/backtest.js';
import { EvalRenderer } from '/js/renderers/eval.js';
import { SignalsRenderer } from '/js/renderers/signals.js';
import { TradesRenderer } from '/js/renderers/trades.js';
import { SettingsRenderer } from '/js/renderers/settings.js';
import { AppLayout } from '/js/layouts/AppLayout.js';
import { Sidebar } from '/js/components/Sidebar.js';
import { Toolbar } from '/js/components/Toolbar.js';
import { StatusBar } from '/js/components/StatusBar.js';

const renderers = {};

// ─── Watchlist Manager ────────────────────────────────────────────────
const WATCHLIST_KEY = 'stock_scanner_watchlist';

function loadWatchlist() {
  try {
    const raw = localStorage.getItem(WATCHLIST_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveWatchlist(items) {
  localStorage.setItem(WATCHLIST_KEY, JSON.stringify(items));
}

function addToWatchlist(code, name) {
  const items = loadWatchlist();
  if (items.some(i => i.code === code)) return false;
  items.push({ code, name: name || code, addedAt: Date.now() });
  saveWatchlist(items);
  renderWatchlist();
  return true;
}

function removeFromWatchlist(code) {
  const items = loadWatchlist().filter(i => i.code !== code);
  saveWatchlist(items);
  renderWatchlist();
}

function isInWatchlist(code) {
  return loadWatchlist().some(i => i.code === code);
}

function renderWatchlist() {
  const container = document.getElementById('watchList');
  if (!container) return;
  const items = loadWatchlist();

  // Clear existing content safely
  while (container.firstChild) container.removeChild(container.firstChild);

  if (items.length === 0) {
    const hint = document.createElement('div');
    hint.style.cssText = 'font-size:12px;color:var(--text-dim);padding:8px 0';
    hint.textContent = '暂无观察标的';
    const sub = document.createElement('div');
    sub.style.cssText = 'font-size:11px;color:var(--text-dim)';
    sub.textContent = '在选股结果中点击股票可添加';
    hint.appendChild(sub);
    container.appendChild(hint);
    return;
  }

  items.forEach(item => {
    const row = document.createElement('div');
    row.className = 'watchlist-item';
    row.dataset.code = item.code;
    row.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);font-size:13px;cursor:pointer';

    const info = document.createElement('div');
    info.style.cssText = 'flex:1;min-width:0';
    info.addEventListener('click', () => openStockDetail(item.code, item.name));

    const nameEl = document.createElement('div');
    nameEl.style.cssText = 'font-weight:500;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis';
    nameEl.textContent = item.name || item.code;
    info.appendChild(nameEl);

    const codeEl = document.createElement('div');
    codeEl.style.cssText = 'font-size:11px;color:var(--text-dim)';
    codeEl.textContent = item.code;
    info.appendChild(codeEl);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'watchlist-remove';
    removeBtn.dataset.code = item.code;
    removeBtn.style.cssText = 'flex-shrink:0;width:20px;height:20px;border:none;background:none;color:var(--text-dim);cursor:pointer;font-size:14px;line-height:1;padding:0;opacity:0.5;transition:opacity 0.15s';
    removeBtn.title = '移除';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      removeFromWatchlist(removeBtn.dataset.code);
    });
    removeBtn.addEventListener('mouseenter', () => { removeBtn.style.opacity = '1'; removeBtn.style.color = 'var(--danger, #ff6b6b)'; });
    removeBtn.addEventListener('mouseleave', () => { removeBtn.style.opacity = '0.5'; removeBtn.style.color = 'var(--text-dim)'; });

    row.appendChild(info);
    row.appendChild(removeBtn);
    container.appendChild(row);
  });
}

// Expose watchlist API globally for other renderers
window.addToWatchlist = addToWatchlist;
window.removeFromWatchlist = removeFromWatchlist;
window.isInWatchlist = isInWatchlist;

// ─── Theme management ─────────────────────────────────────────────────
function applyChartDefaults() {
    if (typeof Chart === 'undefined') return;
    Chart.defaults.color = getThemeColor('--text-sub');
    Chart.defaults.borderColor = getThemeColor('--border');
}

function initTheme() {
    const saved = localStorage.getItem('theme');
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = saved || (prefersDark ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
    applyChartDefaults();
}

function updateWsIndicator(status) {
  const indicator = document.getElementById('wsIndicator');
  if (!indicator) return;
  indicator.className = 'ws-indicator ' + status;
}
async function init() {
  console.log('[App] 初始化...');

  // Initialize new TradingView-style layout
  const app = document.getElementById('app') || document.body;
  const layout = new AppLayout(app);

  // Panel switching function
  function switchPanel(panelId) {
    document.querySelectorAll('.panels-container .panel').forEach(p => p.classList.remove('active'));
    const panel = document.getElementById('panel-' + panelId);
    if (panel) panel.classList.add('active');
    // Sidebar handles its own active state via render() in bindEvents
    // Lazy-load data for panels that need it
    if (panelId === 'reports') loadReportsTab();
    if (panelId === 'trades') renderers.trades?.fetch();
    if (panelId === 'signals') renderers.signals?.fetch();
  }

  const sidebar = new Sidebar(document.getElementById('sidebar'), switchPanel);
  const toolbar = new Toolbar(document.getElementById('toolbar'), (theme) => {
    applyChartDefaults();
    Object.values(renderers).forEach(r => r.update?.());
  });
  const statusbar = new StatusBar(document.getElementById('statusbar'));

  initErrorTracker();
  initTheme();
  renderWatchlist();

  renderers.metrics = new MetricsRenderer();
  renderers.portfolio = new PortfolioRenderer();
  renderers.scanner = new ScannerRenderer();
  renderers.risk = new RiskRenderer();
  renderers.positions = new PositionsRenderer();
  renderers.exposures = new ExposuresRenderer();
  renderers.audit = new AuditRenderer();
  renderers.backtest = new BacktestRenderer();
  renderers.eval = new EvalRenderer();
  renderers.signals = new SignalsRenderer();
  renderers.trades = new TradesRenderer();
  renderers.settings = new SettingsRenderer();

  Object.values(renderers).forEach(r => r.mount?.());
  renderers.backtest.init();
  renderers.signals.fetch();

  wsManager.onStatusChange((status) => {
    updateWsIndicator(status);
    statusbar.setWsStatus(status);
    if (status === 'connected') {
      pollingController.slowDown();
    } else if (status === 'disconnected' || status === 'error') {
      pollingController.start();
    }
  });

  document.getElementById('refreshBtn').addEventListener('click', async () => {
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true; btn.textContent = '加载中...';
    // 显示全局加载指示器
    document.body.classList.add('loading-active');
    const preset = document.getElementById('presetSelect')?.value || 'balanced';
    try {
      const [dashRes, scanRes, riskRes, auditRes, sigRes, expRes] = await apiClient.all([
        ['/api/dashboard', { preset }],
        ['/api/scan', { preset }],
        ['/api/risk', {}],
        ['/api/audit', { limit: 15 }],
        ['/api/signals', { days: 30 }],
        ['/api/exposures', {}],
      ]);
      if (dashRes) stateManager.updateAll({ dashboard: dashRes });
      if (scanRes) stateManager.updateAll({ scan: scanRes });
      if (riskRes) stateManager.updateAll({ risk: riskRes });
      if (auditRes) stateManager.updateAll({ audit: auditRes });
      if (sigRes) stateManager.updateAll({ signals: sigRes });
      if (expRes) stateManager.updateAll({ exposures: expRes });
    } catch (e) {
      console.error('[刷新] 加载失败:', e);
      alert('数据加载失败，请稍后重试');
    } finally {
      btn.disabled = false; btn.textContent = '刷新数据';
      document.body.classList.remove('loading-active');
    }
  });

  initKeyboardShortcuts();
  initExportButtons();
  initNotifications();

  document.getElementById('presetSelect')?.addEventListener('change', () => {
    document.getElementById('refreshBtn')?.click();
  });

  console.log('[App] IIFE 开始...');
  wsManager.connect();

  // 立即主动拉取初始数据（不依赖 WS，WS 只负责后续增量更新）
  (async () => {
    console.log('[App] IIFE 执行...');
    try {
      const [dashRes, riskRes, auditRes, expRes] = await Promise.allSettled([
        apiClient.get('/api/dashboard'),
        apiClient.get('/api/risk'),
        apiClient.get('/api/audit', { limit: 15 }),
        apiClient.get('/api/exposures'),
      ]);
      console.log('[App] IIFE 结果:', dashRes?.status, riskRes?.status, auditRes?.status, expRes?.status);
      if (dashRes.status === 'fulfilled' && dashRes.value) {
        stateManager.set('dashboard', dashRes.value);
      }
      if (riskRes.status === 'fulfilled' && riskRes.value) {
        stateManager.set('risk', riskRes.value);
      }
      if (auditRes.status === 'fulfilled' && auditRes.value) {
        stateManager.set('audit', auditRes.value);
      }
      if (expRes.status === 'fulfilled' && expRes.value) {
        stateManager.set('exposures', expRes.value);
      }
      // scan 单独触发（耗时较长，不阻塞初始加载）
      apiClient.get('/api/scan').then(scanRes => {
        if (scanRes) stateManager.set('scan', scanRes);
      }).catch(() => {});
    } catch (e) {
      console.error('[App] 初始数据拉取失败:', e);
    }
  })();

  // 首次使用引导
  const onboarding = new Onboarding();
  onboarding.start();

  console.log('[App] 初始化完成');
}

function setupTabSwitching() {
  // Legacy: tab switching now handled by Sidebar + switchPanel
}

let _reportsLoaded = false;
async function loadReportsTab() {
  if (_reportsLoaded) return;
  _reportsLoaded = true;
  const loading = document.getElementById('reportsLoading');
  const tbody = document.getElementById('reportsBody');
  const logBody = document.getElementById('pushLogBody');
  try {
    const data = await apiClient.get('/api/reports');
    loading.style.display = 'none';

    // 策略报告
    if (data.reports && data.reports.length > 0) {
      tbody.innerHTML = '';
      data.reports.forEach(r => {
        const totalSignals = Object.values(r.signals || {}).reduce((a, b) => a + b, 0);
        const signalsStr = Object.entries(r.signals || {})
          .map(([k, v]) => `${k}: ${v}`)
          .join(', ');
        const tr = document.createElement('tr');
        [
          r.date || '--',
          r.sentiment != null ? r.sentiment.toFixed(2) : '--',
          String(totalSignals),
          signalsStr || '--',
        ].forEach((txt, i) => {
          const td = document.createElement('td');
          td.textContent = txt;
          if (i === 3) td.style.cssText = 'font-size:12px;color:var(--text-dim);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
          else if (i > 0) td.style.cssText = 'text-align:center';
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    } else {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4;
      td.textContent = '暂无历史报告';
      td.style.cssText = 'color:var(--text-dim);padding:16px';
      tr.appendChild(td);
      tbody.appendChild(tr);
    }

    // 推送日志
    if (data.push_logs && data.push_logs.length > 0) {
      logBody.innerHTML = '';
      data.push_logs.forEach(log => {
        const d = new Date(log.timestamp);
        const timeStr = d.toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai'});
        const statusSpan = document.createElement('span');
        statusSpan.textContent = log.success ? '成功' : '失败';
        statusSpan.style.cssText = log.success ? 'color:#4ecdc4' : 'color:#ff6b6b';
        const typeSpan = document.createElement('span');
        typeSpan.textContent = log.type === 'morning_report' ? '晨间报告' : '策略报告';
        const tr = document.createElement('tr');
        [
          timeStr,
          typeSpan,
          statusSpan,
          log.detail || '--',
        ].forEach((el, i) => {
          const td = document.createElement('td');
          if (typeof el === 'string') td.textContent = el;
          else td.appendChild(el);
          if (i === 0) td.style.cssText = 'font-size:12px';
          else if (i === 3) td.style.cssText = 'font-size:12px;color:var(--text-dim)';
          tr.appendChild(td);
        });
        logBody.appendChild(tr);
      });
    } else {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4;
      td.textContent = '暂无推送记录';
      td.style.cssText = 'color:var(--text-dim);padding:16px';
      tr.appendChild(td);
      logBody.appendChild(tr);
    }
  } catch (e) {
    console.error('[Reports] 加载失败:', e);
    if (loading) loading.textContent = '加载失败';
  }
}

window.openStockDetail = async function(code, name) {
  const modal = document.getElementById('stockModal');
  modal.classList.add('active');
  const titleEl = document.getElementById('stockModalTitle');
  titleEl.textContent = name ? name + ' (' + code + ')' : code;

  // Add/Remove watchlist button
  let wlBtn = document.getElementById('stockModalWatchlistBtn');
  if (!wlBtn) {
    wlBtn = document.createElement('button');
    wlBtn.id = 'stockModalWatchlistBtn';
    wlBtn.style.cssText = 'margin-left:8px;font-size:12px;padding:4px 10px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer;vertical-align:middle';
    titleEl.parentNode.insertBefore(wlBtn, titleEl.nextSibling);
  }
  const inList = isInWatchlist(code);
  wlBtn.textContent = inList ? '★ 已关注' : '☆ 加入观察';
  wlBtn.style.borderColor = inList ? 'var(--accent)' : 'var(--border)';
  wlBtn.style.color = inList ? 'var(--accent)' : 'var(--text)';
  wlBtn.onclick = () => {
    if (isInWatchlist(code)) {
      removeFromWatchlist(code);
      wlBtn.textContent = '☆ 加入观察';
      wlBtn.style.borderColor = 'var(--border)';
      wlBtn.style.color = 'var(--text)';
    } else {
      addToWatchlist(code, name);
      wlBtn.textContent = '★ 已关注';
      wlBtn.style.borderColor = 'var(--accent)';
      wlBtn.style.color = 'var(--accent)';
    }
  };

  ['stockScorePanel','stockFactorsPanel','stockJudgesPanel'].forEach(id => {
    document.getElementById(id).innerHTML = '<div class="loading"><span class="spinner"></span>加载中...</div>';
  });
  try {
    const [scoreRes, stockRes] = await Promise.all([
      apiClient.get('/api/score/' + code),
      apiClient.get('/api/stock/' + code),
    ]);
    if (scoreRes) renderStockScore(scoreRes);
    else renderStockScore({});
    if (stockRes) renderStockFactors(stockRes);
    else renderStockFactors({});
  } catch (e) {
    console.error('[StockDetail] 加载失败:', e);
    ['stockScorePanel','stockFactorsPanel'].forEach(id => {
      const p = document.getElementById(id);
      p.innerHTML = '<div style="padding:20px;color:var(--text-dim)">加载失败，请稍后重试</div>';
    });
  }
};

// 安全文本插入（防止 XSS）
function safeText(parent, tag, css, text) {
  const el = document.createElement(tag);
  if (css) el.style.cssText = css;
  el.textContent = text;
  parent.appendChild(el);
  return el;
}

function renderStockScore(r) {
  const s = r.score || {};
  const jp = r.judge_panel || {};
  const ratingClass = (s.rating==='强烈买入'||s.rating==='买入')?'tag-up':s.rating==='持有'?'tag-up':'tag-warn';
  const panel = document.getElementById('stockScorePanel');
  panel.innerHTML = '';
  const container = document.createElement('div');
  container.style.cssText = 'font-size:13px;line-height:2;color:var(--text-sub)';
  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px';
  // 左格：综合评分
  const left = document.createElement('div');
  left.style.cssText = 'background:var(--card-hover);padding:12px;border-radius:8px';
  safeText(left, 'b', '', '综合评分');
  left.appendChild(document.createElement('br'));
  const scoreEl = document.createElement('span');
  scoreEl.style.cssText = 'font-size:28px;font-weight:700';
  scoreEl.textContent = s.score || '--';
  left.appendChild(scoreEl);
  left.appendChild(document.createElement('br'));
  const ratingEl = document.createElement('span');
  ratingEl.className = 'tag ' + ratingClass;
  ratingEl.textContent = s.rating || '--';
  left.appendChild(ratingEl);
  grid.appendChild(left);
  // 右格：裁判团
  const right = document.createElement('div');
  right.style.cssText = 'background:var(--card-hover);padding:12px;border-radius:8px';
  safeText(right, 'b', '', '裁判团');
  right.appendChild(document.createElement('br'));
  const consensusEl = document.createElement('span');
  consensusEl.style.cssText = 'font-size:28px;font-weight:700';
  consensusEl.textContent = jp.consensus || 'HOLD';
  right.appendChild(consensusEl);
  right.appendChild(document.createElement('br'));
  safeText(right, 'span', '', '评分: ' + (jp.score || '--') + ' | 看多: ' + (jp.bullish_pct || 0) + '%');
  grid.appendChild(right);
  container.appendChild(grid);
  panel.appendChild(container);
}

function renderStockFactors(r) {
  const factors = r.factors || {};
  const panel = document.getElementById('stockFactorsPanel');
  panel.innerHTML = '';
  const container = document.createElement('div');
  container.style.cssText = 'font-size:13px;line-height:1.8;color:var(--text-sub)';
  safeText(container, 'b', '', '因子表');
  container.appendChild(document.createElement('br'));
  Object.entries(factors).forEach(([k, v]) => {
    const tag = document.createElement('span');
    tag.className = 'tag';
    tag.style.cssText = 'background:rgba(88,166,255,0.12);color:var(--accent)';
    tag.textContent = k;
    container.appendChild(tag);
    const val = typeof v === 'number' ? v.toFixed(2) : v;
    safeText(container, 'span', '', ' ' + val + ' ');
  });
  panel.appendChild(container);
}

// Keyboard shortcuts
function initExportButtons() {
  async function downloadCsv(url, filename) {
    try {
      const apiKey = sessionStorage.getItem('api_key') || 'dev';
      const res = await fetch(url, { headers: { 'X-API-Key': apiKey } });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      console.error('[Export] 下载失败:', e);
      alert('导出失败，请稍后重试');
    }
  }
  document.getElementById('exportPositionsBtn')?.addEventListener('click', () => {
    downloadCsv('/api/export/positions', 'positions.csv');
  });
  document.getElementById('exportTradesBtn')?.addEventListener('click', () => {
    downloadCsv('/api/export/trades', 'trades.csv');
  });
  document.getElementById('exportPositionsExcelBtn')?.addEventListener('click', () => {
    downloadCsv('/api/export/positions/excel', 'positions.xlsx');
  });
  document.getElementById('exportTradesExcelBtn')?.addEventListener('click', () => {
    downloadCsv('/api/export/trades/excel', 'trades.xlsx');
  });

  // Config JSON export/import
  document.getElementById('exportConfigBtn')?.addEventListener('click', async () => {
    const preset = document.getElementById('presetSelect')?.value || 'balanced';
    downloadCsv(`/api/export/config?preset=${preset}`, `config_${preset}.json`);
  });

  document.getElementById('importConfigBtn')?.addEventListener('click', () => {
    document.getElementById('configFileInput')?.click();
  });

  document.getElementById('configFileInput')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text);
      const apiKey = sessionStorage.getItem('api_key') || 'dev';
      const resp = await fetch('/api/import/config', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': apiKey,
        },
        body: JSON.stringify(data),
      });
      const result = await resp.json();
      if (!resp.ok) throw new Error(result.detail || '导入失败');
      alert(result.message || '导入成功');
    } catch (err) {
      console.error('[Config Import] 失败:', err);
      alert('导入失败: ' + err.message);
    }
    // Reset file input so the same file can be re-imported
    e.target.value = '';
  });
}

function initKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
      if (e.key === 'Escape') { e.target.blur(); }
      return;
    }
    switch (e.key) {
      case 'j': e.preventDefault(); navigateTab(1); break;
      case 'k': e.preventDefault(); navigateTab(-1); break;
      case '/': e.preventDefault(); document.querySelector('input[type="search"], #globalSearch')?.focus(); break;
      case 'Escape':
        e.preventDefault();
        document.querySelectorAll('details[open]').forEach(d => d.removeAttribute('open'));
        document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
        document.querySelectorAll('.modal.active').forEach(m => m.classList.remove('active'));
        break;
      case 'r':
        if (!e.ctrlKey && !e.metaKey) { e.preventDefault(); document.getElementById('refreshBtn')?.click(); }
        break;
      case '?': e.preventDefault(); showShortcutsHelp(); break;
    }
  });
}

function navigateTab(direction) {
  const tabs = [...document.querySelectorAll('.nav-item')];
  const activeIdx = tabs.findIndex(t => t.classList.contains('active'));
  const nextIdx = Math.max(0, Math.min(tabs.length - 1, activeIdx + direction));
  if (nextIdx !== activeIdx) tabs[nextIdx]?.click();
}

function showShortcutsHelp() {
  const existing = document.getElementById('shortcutsHelp');
  if (existing) { existing.style.display = existing.style.display === 'none' ? 'block' : 'none'; return; }

  const shortcuts = [
    ['j', '下一个 tab'], ['k', '上一个 tab'], ['/', '聚焦搜索'],
    ['Esc', '关闭弹窗'], ['r', '刷新数据'], ['?', '显示此帮助'],
  ];
  const div = document.createElement('div');
  div.id = 'shortcutsHelp';
  div.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;z-index:1000;min-width:300px;box-shadow:0 8px 32px rgba(0,0,0,0.3)';
  const title = document.createElement('h3');
  title.style.cssText = 'margin:0 0 12px';
  title.textContent = '键盘快捷键';
  div.appendChild(title);
  const table = document.createElement('table');
  table.style.cssText = 'width:100%;font-size:13px';
  shortcuts.forEach(([key, desc]) => {
    const tr = document.createElement('tr');
    const tdKey = document.createElement('td');
    const kbd = document.createElement('kbd');
    kbd.textContent = key;
    tdKey.appendChild(kbd);
    const tdDesc = document.createElement('td');
    tdDesc.textContent = desc;
    tr.appendChild(tdKey);
    tr.appendChild(tdDesc);
    table.appendChild(tr);
  });
  div.appendChild(table);
  const footer = document.createElement('div');
  footer.style.cssText = 'text-align:right;margin-top:12px';
  const closeBtn = document.createElement('button');
  closeBtn.textContent = '关闭';
  closeBtn.style.cssText = 'padding:4px 12px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer';
  closeBtn.addEventListener('click', () => div.remove());
  footer.appendChild(closeBtn);
  div.appendChild(footer);
  document.body.appendChild(div);
}

window.openAuditDetail = async function(id) {
  document.getElementById('auditModal').classList.add('active');
  document.getElementById('auditModalTitle').textContent = '扫描审计 #' + id;
  const detailPanel = document.getElementById('auditDetailPanel');
  detailPanel.innerHTML = '';
  const loading = document.createElement('div');
  loading.className = 'loading';
  loading.textContent = '加载中...';
  detailPanel.appendChild(loading);
  try {
    const r = await apiClient.get('/api/audit/' + id);
    detailPanel.innerHTML = '';
    if (!r || r.error) {
      const errDiv = document.createElement('div');
      errDiv.style.cssText = 'padding:20px;color:var(--text-dim)';
      errDiv.textContent = r?.error || '请求失败';
      detailPanel.appendChild(errDiv);
      return;
    }
    const container = document.createElement('div');
    container.style.cssText = 'font-size:13px;line-height:1.8;color:var(--text-sub)';
    const rows = [
      ['扫描时间', r.scan_time || '--'],
      ['策略预设', r.preset || '--'],
      ['候选股数', r.total_candidates || 0],
      ['风控建议', r.risk_recommendation || '--'],
      ['组合夏普', r.portfolio_sharpe != null ? r.portfolio_sharpe.toFixed(2) : '--'],
      ['持仓数', r.portfolio_n_positions || 0],
    ];
    rows.forEach(([label, value]) => {
      const row = document.createElement('div');
      row.className = 'detail-row';
      const lbl = document.createElement('span');
      lbl.className = 'label';
      lbl.textContent = label;
      const val = document.createElement('span');
      val.className = 'value';
      val.textContent = value;
      row.appendChild(lbl);
      row.appendChild(val);
      container.appendChild(row);
    });
    detailPanel.appendChild(container);
  } catch (e) {
    console.error('[AuditDetail] 加载失败:', e);
    detailPanel.innerHTML = '';
    const errDiv = document.createElement('div');
    errDiv.style.cssText = 'padding:20px;color:var(--text-dim)';
    errDiv.textContent = '加载失败，请稍后重试';
    detailPanel.appendChild(errDiv);
  }
};

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.modal-close').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.closest('.modal-overlay')?.classList.remove('active');
    });
  });
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.classList.remove('active');
    });
  });
  init();
});
