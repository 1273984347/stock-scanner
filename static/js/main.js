import { wsManager } from './websocket.js';
import { stateManager } from './state.js';
import { apiClient } from './api.js';
import { pollingController } from './polling.js';
import { MetricsRenderer } from './renderers/metrics.js';
import { PortfolioRenderer } from './renderers/portfolio.js';
import { ScannerRenderer } from './renderers/scanner.js';
import { RiskRenderer } from './renderers/risk.js';
import { PositionsRenderer } from './renderers/positions.js';
import { ExposuresRenderer } from './renderers/exposures.js';
import { AuditRenderer } from './renderers/audit.js';
import { BacktestRenderer } from './renderers/backtest.js';
import { EvalRenderer } from './renderers/eval.js';
import { SignalsRenderer } from './renderers/signals.js';
import { TradesRenderer } from './renderers/trades.js';

const renderers = {};

function updateWsIndicator(status) {
  const indicator = document.getElementById('wsIndicator');
  if (!indicator) return;
  indicator.className = 'ws-indicator ' + status;
}
async function init() {
  console.log('[App] 初始化...');

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

  Object.values(renderers).forEach(r => r.mount?.());
  renderers.backtest.init();
  renderers.signals.fetch();

  wsManager.onStatusChange((status) => {
    updateWsIndicator(status);
    if (status === 'connected') {
      pollingController.stop();
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
      const [dashRes, scanRes, riskRes, auditRes, sigRes] = await apiClient.all([
        ['/api/dashboard', { preset }],
        ['/api/scan', { preset }],
        ['/api/risk', {}],
        ['/api/audit', { limit: 15 }],
        ['/api/signals', { days: 30 }],
      ]);
      if (dashRes) stateManager.updateAll({ dashboard: dashRes });
      if (scanRes) stateManager.updateAll({ scan: scanRes });
      if (riskRes) stateManager.updateAll({ risk: riskRes });
      if (auditRes) stateManager.updateAll({ audit: auditRes });
      if (sigRes) stateManager.updateAll({ signals: sigRes });
    } catch (e) {
      console.error('[刷新] 加载失败:', e);
      alert('数据加载失败，请稍后重试');
    } finally {
      btn.disabled = false; btn.textContent = '刷新数据';
      document.body.classList.remove('loading-active');
    }
  });

  setupTabSwitching();

  document.getElementById('presetSelect')?.addEventListener('change', () => {
    document.getElementById('refreshBtn')?.click();
  });

  console.log('[App] IIFE 开始...');
  wsManager.connect();

  // 立即主动拉取初始数据（不依赖 WS，WS 只负责后续增量更新）
  (async () => {
    console.log('[App] IIFE 执行...');
    try {
      const [dashRes, riskRes, auditRes] = await Promise.allSettled([
        apiClient.get('/api/dashboard'),
        apiClient.get('/api/risk'),
        apiClient.get('/api/audit', { limit: 15 }),
      ]);
      console.log('[App] IIFE 结果:', dashRes?.status, riskRes?.status, auditRes?.status);
      if (dashRes.status === 'fulfilled' && dashRes.value) {
        stateManager.set('dashboard', dashRes.value);
        console.log('[App] dashboard 已设置');
      }
      if (riskRes.status === 'fulfilled' && riskRes.value) {
        stateManager.set('risk', riskRes.value);
      }
      if (auditRes.status === 'fulfilled' && auditRes.value) {
        stateManager.set('audit', auditRes.value);
      }
    } catch (e) {
      console.error('[App] 初始数据拉取失败:', e);
    }
  })();

  console.log('[App] 初始化完成');
}

function setupTabSwitching() {
  const navTabs = document.querySelectorAll('.nav-tab');
  const panels = document.querySelectorAll('.panel');
  navTabs.forEach(tab => {
    tab.addEventListener('click', () => {
      navTabs.forEach(t => t.classList.remove('active'));
      panels.forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('panel-' + tab.dataset.panel)?.classList.add('active');
      if (tab.dataset.panel === 'reports') {
        loadReportsTab();
      }
      if (tab.dataset.panel === 'trades') {
        renderers.trades?.fetch();
      }
    });
  });
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
  document.getElementById('stockModalTitle').textContent = name ? name + ' (' + code + ')' : code;
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
  // ESC 键关闭弹窗
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-overlay.active').forEach(modal => {
        modal.classList.remove('active');
      });
    }
  });
  init();
});
