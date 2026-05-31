export class AppLayout {
  constructor(container) {
    this.container = container;
    this.render();
  }

  render() {
    this.container.innerHTML = `
      <div class="app-layout">
        <aside class="sidebar" id="sidebar"></aside>
        <div class="main-area">
          <header class="toolbar" id="toolbar"></header>
          <div class="content-area">
            <div class="chart-area" id="chart-area">
              <div class="metrics-bar">
                <div class="metric-card" title="市场情绪指数"><div class="metric-label">市场情绪</div><div class="metric-value" id="sentiment">--</div><div class="metric-sub" id="sentimentLabel">加载中</div></div>
                <div class="metric-card" title="宏观门控"><div class="metric-label">宏观门控</div><div class="metric-value" id="macroState">--</div><div class="metric-sub" id="macroLabel">加载中</div></div>
                <div class="metric-card" title="建议仓位"><div class="metric-label">建议仓位</div><div class="metric-value" id="exposure">--</div><div class="metric-sub" id="exposureLabel">加载中</div></div>
                <div class="metric-card" title="组合夏普"><div class="metric-label">组合夏普</div><div class="metric-value" id="sharpe">--</div><div class="metric-sub">预期夏普比</div></div>
                <div class="metric-card" title="持仓数"><div class="metric-label">持仓数</div><div class="metric-value" id="nPositions">--</div><div class="metric-sub">优化组合</div></div>
                <div class="metric-card" title="候选股"><div class="metric-label">候选股</div><div class="metric-value" id="totalCand">--</div><div class="metric-sub" id="candLabel">全部策略</div></div>
              </div>
              <div class="panels-container">
                <div class="panel active" id="panel-portfolio">
                  <div class="portfolio-grid">
                    <div class="portfolio-card"><div class="metric-label">优化组合配置</div><div class="table-wrap"><table id="portfolioTable"><thead><tr><th>代码</th><th>名称</th><th>行业</th><th>权重</th><th>评分</th></tr></thead><tbody id="portfolioBody"></tbody></table></div></div>
                    <div class="portfolio-card"><div class="metric-label">行业暴露</div><canvas id="sectorChart"></canvas></div>
                  </div>
                </div>
                <div class="panel" id="panel-scanner">
                  <div class="strategy-tabs" id="strategyTabs"></div>
                  <div class="portfolio-card"><div class="metric-label">选股结果</div><div class="table-wrap"><table id="resultTable"><thead id="resultHead"></thead><tbody id="resultBody"></tbody></table></div></div>
                </div>
                <div class="panel" id="panel-risk"><div class="metric-label">风控中心</div><div id="riskAlerts"></div></div>
                <div class="panel" id="panel-positions"><div class="metric-label">持仓概况</div><div class="table-wrap"><table id="posTable"><thead><tr><th>代码</th><th>名称</th><th>行业</th><th>持仓</th><th>成本</th><th>现价</th><th>盈亏</th></tr></thead><tbody id="posBody"></tbody></table></div></div>
                <div class="panel" id="panel-exposures"><div class="metric-label">因子暴露</div><canvas id="exposureChart"></canvas></div>
                <div class="panel" id="panel-eval"><div class="metric-label">因子评估</div><canvas id="evalChart"></canvas></div>
                <div class="panel" id="panel-audit"><div class="metric-label">决策审计</div><div id="auditList"></div></div>
                <div class="panel" id="panel-backtest"><div class="metric-label">策略回测</div><canvas id="backtestChart"></canvas></div>
                <div class="panel" id="panel-reports"><div class="metric-label">历史报告</div><div id="reportsList"></div></div>
                <div class="panel" id="panel-signals"><div class="metric-label">信号追踪</div><canvas id="signalsChart"></canvas></div>
                <div class="panel" id="panel-trades"><div class="metric-label">交易历史</div><div class="table-wrap"><table id="tradesTable"><thead><tr><th>时间</th><th>代码</th><th>操作</th><th>价格</th><th>盈亏</th></tr></thead><tbody id="tradesBody"></tbody></table></div></div>
                <div class="panel" id="panel-settings"><div class="metric-label">设置</div><div id="settingsContent"></div></div>
              </div>
            </div>
            <aside class="right-panel" id="right-panel">
              <div class="panel-section"><div class="panel-title">快捷操作</div><button id="refreshBtn" class="refresh-btn">刷新数据</button></div>
              <div class="panel-section"><div class="panel-title">观察列表</div><div id="watchList">加载中...</div></div>
            </aside>
          </div>
          <footer class="statusbar" id="statusbar"></footer>
        </div>
      </div>
      <div class="modal-overlay" id="stockModal"><div class="modal"><h3 id="stockModalTitle">个股详情</h3><button class="modal-close">&times;</button><div id="stockDetailContent"></div></div></div>
      <div class="modal-overlay" id="auditModal"><div class="modal"><h3 id="auditModalTitle">审计详情</h3><button class="modal-close">&times;</button><div id="auditDetailContent"></div></div></div>
    `;
  }
}
