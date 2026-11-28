import { stateManager } from '/js/state.js';
import { apiClient } from '/js/api.js';
import { esc } from '/js/renderers/utils.js';

/**
 * RiskRenderer — 风控中心面板渲染器
 * 负责宏观门控 + VaR/CVaR + 压力测试 + 风控建议 + 风险暴露仪表盘
 */
class RiskRenderer {
  constructor() {
    this._unsubscribers = [];
  }

  mount() {
    this._unsubscribers.push(
      stateManager.subscribe('risk', (r) => this.render(r))
    );
    this._loadRiskExposure();
    this._loadCorrelationMatrix();
  }

  render(r) {
    if (!r) return;
    this._renderAlerts(r);
    this._renderStressTests(r.stress_tests || {});
    this._renderRecommendation(r);
  }

  _renderAlerts(r) {
    const alertsEl = document.getElementById('riskAlerts');
    alertsEl.innerHTML = '';

    // 宏观门控
    const mg = r.macro_gate || {};
    const mgClass = mg.state === 'BLOCK_NEW_RISK' ? 'tag-down' : mg.state === 'REDUCE_RISK' ? 'tag-warn' : 'tag-up';
    alertsEl.appendChild(this._alertCard('宏观门控',
      `<span class="tag ${esc(mgClass)}">${esc(mg.state || 'UNKNOWN')}</span><br>${esc(mg.reasoning || '--')}<br>仓位乘数: ${esc(((mg.multiplier || 1) * 100).toFixed(0))}%`
    ));

    // 风险预算
    const rb = r.risk_budget || {};
    alertsEl.appendChild(this._alertCard('风险预算',
      `建议仓位: ${esc(((rb.equity || 0) * 100).toFixed(0))}%<br>最多持仓: ${esc(rb.max_positions || '--')} 只<br>波动体制: ${esc(r.vol_regime || '--')}`
    ));

    // VaR/CVaR
    const varData = r.var || {};
    let varHtml = varData.var_95_pct != null
      ? `VaR(95%): <b class="text-down">${esc((varData.var_95_pct * 100).toFixed(2))}%</b><br>CVaR(95%): <b class="text-down">${esc((varData.cvar_95_pct * 100).toFixed(2))}%</b><br>VaR金额: ¥${esc((varData.var_95 || 0).toFixed(0))}`
      : '暂无持仓数据';
    alertsEl.appendChild(this._alertCard('VaR / CVaR', varHtml));

    // 分散化
    const corrData = r.correlation || {};
    let corrHtml = corrData.avg_correlation != null
      ? `平均相关性: <b>${esc(corrData.avg_correlation.toFixed(3))}</b><br>分散化评分: <b>${esc((corrData.diversification_score || 0).toFixed(2))}</b>`
      : '暂无持仓数据';
    alertsEl.appendChild(this._alertCard('相关性 / 分散化', corrHtml));

    // 风控预警
    const riskAlerts = r.alerts || [];
    if (riskAlerts.length) {
      let alertHtml = '';
      riskAlerts.forEach(a => {
        const cls = a.level === 'CRITICAL' ? 'tag-down' : a.level === 'WARNING' ? 'tag-warn' : 'tag-up';
        alertHtml += `<span class="tag ${esc(cls)}">${esc(a.level || '')}</span> ${esc(a.message || '')}<br>`;
      });
      alertsEl.appendChild(this._alertCard('风控预警', alertHtml));
    }
  }

  _alertCard(title, body) {
    const div = document.createElement('div');
    div.className = 'risk-alert';
    const titleEl = document.createElement('div');
    titleEl.className = 'risk-alert-title';
    titleEl.textContent = title;
    const bodyEl = document.createElement('div');
    bodyEl.className = 'risk-alert-body';
    bodyEl.innerHTML = body;
    div.appendChild(titleEl);
    div.appendChild(bodyEl);
    return div;
  }

  _renderStressTests(stress) {
    const stressEl = document.getElementById('stressTests');
    stressEl.innerHTML = '';
    Object.entries(stress).forEach(([k, v]) => {
      const card = document.createElement('div');
      card.className = 'stress-card';
      const h4 = document.createElement('h4');
      h4.textContent = v.description || k;
      const p1 = document.createElement('p');
      p1.textContent = v.impact || '';
      const p2 = document.createElement('p');
      p2.className = 'action';
      p2.textContent = v.action || '';
      card.appendChild(h4);
      card.appendChild(p1);
      card.appendChild(p2);
      stressEl.appendChild(card);
    });
  }

  _renderRecommendation(r) {
    const recEl = document.getElementById('riskRecommendation');
    const state = r.macro_gate?.state;
    if (state === 'BLOCK_NEW_RISK') {
      recEl.innerHTML = '<span class="tag tag-down">严重预警</span> 建议暂停新开仓，减仓至安全水平';
      recEl.style.color = 'var(--down)';
    } else if (state === 'REDUCE_RISK') {
      recEl.innerHTML = '<span class="tag tag-warn">谨慎操作</span> 建议控制仓位，避免追高';
      recEl.style.color = 'var(--warn)';
    } else {
      recEl.innerHTML = '<span class="tag tag-up">风控通过</span> 可正常交易，注意组合分散化';
      recEl.style.color = 'var(--up)';
    }
  }

  // ---- 风险暴露仪表盘 ----

  async _loadRiskExposure() {
    try {
      const data = await apiClient.get('/api/risk-exposure');
      if (data) this._renderRiskExposure(data);
    } catch (e) {
      console.error('[Risk] exposure load failed:', e);
    }
  }

  _renderRiskExposure(data) {
    const container = document.getElementById('riskExposureSection');
    if (!container) return;

    const var95 = data.var?.var_95_pct || 0;
    const cvar95 = data.var?.cvar_95_pct || 0;
    const avgCorr = data.correlation?.avg_correlation || 0;
    const divScore = data.correlation?.diversification_score || 0;
    const nPos = data.n_positions || 0;
    const pairs = data.correlation?.high_corr_pairs || [];

    const metricsData = [
      { label: 'VaR(95%)', value: (var95 * 100).toFixed(2) + '%' },
      { label: 'CVaR(95%)', value: (cvar95 * 100).toFixed(2) + '%' },
      { label: '平均相关性', value: avgCorr.toFixed(3) },
      { label: '分散化评分', value: divScore.toFixed(1) },
    ];

    const grid = document.createElement('div');
    grid.style.cssText = 'display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:12px';

    metricsData.forEach(m => {
      const card = document.createElement('div');
      card.className = 'portfolio-card';
      const label = document.createElement('div');
      label.className = 'metric-label';
      label.textContent = m.label;
      const value = document.createElement('div');
      value.className = 'metric-value';
      value.textContent = m.value;
      card.appendChild(label);
      card.appendChild(value);
      grid.appendChild(card);
    });

    container.innerHTML = '';
    container.appendChild(grid);

    const info = document.createElement('div');
    info.style.cssText = 'font-size:12px;color:var(--text-dim)';
    const breachedVar = data.var?.var_breached ? '是' : '否';
    const breachedCvar = data.var?.cvar_breached ? '是' : '否';
    let infoText = `持仓数: ${nPos} | VaR breached: ${breachedVar} | CVaR breached: ${breachedCvar}`;
    if (pairs.length) {
      infoText += ' | 高相关对: ' + pairs.map(p => `${p[0]}/${p[1]} (${(p[2] || 0).toFixed(2)})`).join(', ');
    }
    info.textContent = infoText;
    container.appendChild(info);

    container.style.display = 'block';
  }

  async _loadCorrelationMatrix() {
    try {
      const data = await apiClient.get('/api/risk/correlation');
      if (data && data.codes && data.matrix) this._renderCorrelationHeatmap(data);
    } catch (e) {
      console.error('[Risk] correlation load failed:', e);
    }
  }

  _renderCorrelationHeatmap(data) {
    const canvas = document.getElementById('correlationHeatmap');
    if (!canvas) return;
    if (this._corrChart) this._corrChart.destroy();

    const { codes, matrix } = data;
    if (codes.length < 2) return;

    // 构建热力图数据（Chart.js 矩阵图）
    const points = [];
    for (let i = 0; i < codes.length; i++) {
      for (let j = 0; j < codes.length; j++) {
        points.push({ x: j, y: i, v: matrix[i][j] });
      }
    }

    // 使用气泡图模拟热力图
    const colors = points.map(p => {
      const v = p.v;
      if (v > 0.5) return 'rgba(220,80,80,0.8)';
      if (v > 0.3) return 'rgba(255,165,0,0.6)';
      if (v > -0.1) return 'rgba(128,128,128,0.3)';
      return 'rgba(80,200,120,0.6)';
    });

    this._corrChart = new Chart(canvas.getContext('2d'), {
      type: 'bubble',
      data: {
        datasets: [{
          data: points.map(p => ({ x: p.x, y: p.y, r: Math.abs(p.v) * 15 + 3 })),
          backgroundColor: colors,
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => {
                const p = points[ctx.dataIndex];
                return `${codes[p.y]} vs ${codes[p.x]}: ${p.v.toFixed(3)}`;
              },
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            min: -0.5,
            max: codes.length - 0.5,
            ticks: {
              stepSize: 1,
              callback: (v) => codes[v] || '',
            },
          },
          y: {
            type: 'linear',
            min: -0.5,
            max: codes.length - 0.5,
            ticks: {
              stepSize: 1,
              callback: (v) => codes[v] || '',
            },
            reverse: true,
          },
        },
      },
    });
  }

  unmount() {
    this._unsubscribers.forEach(unsub => unsub());
    this._corrChart?.destroy();
  }
}

export { RiskRenderer };
