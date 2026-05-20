import { stateManager } from '../state.js';

/**
 * RiskRenderer — 风控中心面板渲染器
 * 负责宏观门控 + VaR/CVaR + 压力测试 + 风控建议
 */
class RiskRenderer {
  constructor() {
    this._unsubscribers = [];
  }

  mount() {
    this._unsubscribers.push(
      stateManager.subscribe('risk', (r) => this.render(r))
    );
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
      `<span class="tag ${mgClass}">${mg.state || 'UNKNOWN'}</span><br>${mg.reasoning || '--'}<br>仓位乘数: ${((mg.multiplier || 1) * 100).toFixed(0)}%`
    ));

    // 风险预算
    const rb = r.risk_budget || {};
    alertsEl.appendChild(this._alertCard('风险预算',
      `建议仓位: ${((rb.equity || 0) * 100).toFixed(0)}%<br>最多持仓: ${rb.max_positions || '--'} 只<br>波动体制: ${r.vol_regime || '--'}`
    ));

    // VaR/CVaR
    const varData = r.var || {};
    let varHtml = varData.var_95_pct != null
      ? `VaR(95%): <b class="text-down">${(varData.var_95_pct * 100).toFixed(2)}%</b><br>CVaR(95%): <b class="text-down">${(varData.cvar_95_pct * 100).toFixed(2)}%</b><br>VaR金额: ¥${(varData.var_95 || 0).toFixed(0)}`
      : '暂无持仓数据';
    alertsEl.appendChild(this._alertCard('VaR / CVaR', varHtml));

    // 分散化
    const corrData = r.correlation || {};
    let corrHtml = corrData.avg_correlation != null
      ? `平均相关性: <b>${corrData.avg_correlation.toFixed(3)}</b><br>分散化评分: <b>${(corrData.diversification_score || 0).toFixed(2)}</b>`
      : '暂无持仓数据';
    alertsEl.appendChild(this._alertCard('相关性 / 分散化', corrHtml));

    // 风控预警
    const riskAlerts = r.alerts || [];
    if (riskAlerts.length) {
      let alertHtml = '';
      riskAlerts.forEach(a => {
        const cls = a.level === 'CRITICAL' ? 'tag-down' : a.level === 'WARNING' ? 'tag-warn' : 'tag-up';
        alertHtml += `<span class="tag ${cls}">${a.level}</span> ${a.message}<br>`;
      });
      alertsEl.appendChild(this._alertCard('风控预警', alertHtml));
    }
  }

  _alertCard(title, body) {
    const div = document.createElement('div');
    div.className = 'risk-alert';
    div.innerHTML = `<div class="risk-alert-title">${title}</div><div class="risk-alert-body">${body}</div>`;
    return div;
  }

  _renderStressTests(stress) {
    const stressEl = document.getElementById('stressTests');
    stressEl.innerHTML = '';
    Object.entries(stress).forEach(([k, v]) => {
      const card = document.createElement('div');
      card.className = 'stress-card';
      card.innerHTML = `<h4>${v.description}</h4><p>${v.impact}</p><p class="action">${v.action}</p>`;
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

  unmount() {
    this._unsubscribers.forEach(unsub => unsub());
  }
}

export { RiskRenderer };
