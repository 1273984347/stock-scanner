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

  Object.values(renderers).forEach(r => r.mount?.());
  renderers.backtest.init();

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
    const preset = document.getElementById('presetSelect')?.value || 'balanced';
    try {
      const [dashRes, scanRes, riskRes, auditRes] = await apiClient.all([
        ['/api/dashboard', { preset }],
        ['/api/scan', { preset }],
        ['/api/risk', {}],
        ['/api/audit', { limit: 15 }],
      ]);
      if (dashRes) stateManager.updateAll({ dashboard: dashRes });
      if (scanRes) stateManager.updateAll({ scan: scanRes });
      if (riskRes) stateManager.updateAll({ risk: riskRes });
      if (auditRes) stateManager.updateAll({ audit: auditRes });
    } finally {
      btn.disabled = false; btn.textContent = '刷新数据';
    }
  });

  setupTabSwitching();

  document.getElementById('presetSelect')?.addEventListener('change', () => {
    document.getElementById('refreshBtn')?.click();
  });

  wsManager.connect();

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
    });
  });
}

window.openStockDetail = async function(code, name) {
  const modal = document.getElementById('stockModal');
  modal.classList.add('active');
  document.getElementById('stockModalTitle').textContent = name ? name + ' (' + code + ')' : code;
  ['stockScorePanel','stockFactorsPanel','stockJudgesPanel'].forEach(id => {
    document.getElementById(id).innerHTML = '<div class="loading"><span class="spinner"></span>加载中...</div>';
  });
  const [scoreRes, stockRes] = await Promise.all([
    apiClient.get('/api/score/' + code),
    apiClient.get('/api/stock/' + code),
  ]);
  if (scoreRes) renderStockScore(scoreRes);
  if (stockRes) renderStockFactors(stockRes);
};

function renderStockScore(r) {
  const s = r.score || {};
  const jp = r.judge_panel || {};
  const ratingClass = (s.rating==='强烈买入'||s.rating==='买入')?'tag-up':s.rating==='持有'?'tag-up':'tag-warn';
  document.getElementById('stockScorePanel').innerHTML =
    '<div style="font-size:13px;line-height:2;color:var(--text-sub)">' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px">' +
    '<div style="background:var(--card-hover);padding:12px;border-radius:8px">' +
    '<b>综合评分</b><br><span style="font-size:28px;font-weight:700">' + (s.score || '--') + '</span>/10<br>' +
    '<span class="tag ' + ratingClass + '">' + (s.rating || '--') + '</span>' +
    '</div>' +
    '<div style="background:var(--card-hover);padding:12px;border-radius:8px">' +
    '<b>裁判团</b><br><span style="font-size:28px;font-weight:700">' + (jp.consensus || 'HOLD') + '</span><br>评分: ' + (jp.score || '--') + ' | 看多: ' + (jp.bullish_pct || 0) + '%' +
    '</div>' +
    '</div>' +
    '</div>';
}

function renderStockFactors(r) {
  const factors = r.factors || {};
  const factorHtml = Object.entries(factors).map(([k, v]) => '<span class="tag" style="background:rgba(88,166,255,0.12);color:var(--accent)">' + k + '</span> ' + (typeof v === 'number' ? v.toFixed(2) : v) + ' ').join('');
  document.getElementById('stockFactorsPanel').innerHTML =
    '<div style="font-size:13px;line-height:1.8;color:var(--text-sub)">' +
    '<b>因子表</b><br>' + factorHtml +
    '</div>';
}

window.openAuditDetail = async function(id) {
  document.getElementById('auditModal').classList.add('active');
  document.getElementById('auditModalTitle').textContent = '扫描审计 #' + id;
  document.getElementById('auditDetailPanel').innerHTML = '<div class="loading"><span class="spinner"></span>加载中...</div>';
  const r = await apiClient.get('/api/audit/' + id);
  if (!r || r.error) { document.getElementById('auditDetailPanel').innerHTML = '<div style="padding:20px;color:var(--text-dim)">' + (r?.error || '请求失败') + '</div>'; return; }
  document.getElementById('auditDetailPanel').innerHTML =
    '<div style="font-size:13px;line-height:1.8;color:var(--text-sub)">' +
    '<div class="detail-row"><span class="label">扫描时间</span><span class="value">' + (r.scan_time || '--') + '</span></div>' +
    '<div class="detail-row"><span class="label">策略预设</span><span class="value">' + (r.preset || '--') + '</span></div>' +
    '<div class="detail-row"><span class="label">候选股数</span><span class="value">' + (r.total_candidates || 0) + '</span></div>' +
    '<div class="detail-row"><span class="label">风控建议</span><span class="value">' + (r.risk_recommendation || '--') + '</span></div>' +
    '<div class="detail-row"><span class="label">组合夏普</span><span class="value">' + (r.portfolio_sharpe != null ? r.portfolio_sharpe.toFixed(2) : '--') + '</span></div>' +
    '<div class="detail-row"><span class="label">持仓数</span><span class="value">' + (r.portfolio_n_positions || 0) + '</span></div>' +
    '</div>';
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
