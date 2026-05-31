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
                <div class="metric-card"><div class="metric-label">市场情绪</div><div class="metric-value" id="sentiment">--</div><div class="metric-sub" id="sentimentLabel">加载中</div></div>
                <div class="metric-card"><div class="metric-label">宏观门控</div><div class="metric-value" id="macroState">--</div><div class="metric-sub" id="macroLabel">加载中</div></div>
                <div class="metric-card"><div class="metric-label">建议仓位</div><div class="metric-value" id="exposure">--</div><div class="metric-sub" id="exposureLabel">加载中</div></div>
                <div class="metric-card"><div class="metric-label">组合夏普</div><div class="metric-value" id="sharpe">--</div><div class="metric-sub">预期夏普比</div></div>
                <div class="metric-card"><div class="metric-label">持仓数</div><div class="metric-value" id="nPositions">--</div><div class="metric-sub">优化组合</div></div>
                <div class="metric-card"><div class="metric-label">候选股</div><div class="metric-value" id="totalCand">--</div><div class="metric-sub" id="candLabel">全部策略</div></div>
              </div>
              <div class="panels-container">
                <!-- 组合优化面板 -->
  <div class="panel active" id="panel-portfolio">
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">优化组合配置</div>
        <div class="table-wrap"><table id="portfolioTable"><thead><tr><th>代码</th><th>名称</th><th>行业</th><th>权重</th><th>评分</th></tr></thead><tbody id="portfolioBody"></tbody></table></div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">行业暴露</div>
        <canvas id="sectorChart"></canvas>
      </div>
    </div>
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">组合预期收益 vs 波动率</div>
        <canvas id="returnVolChart"></canvas>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">组合指标</div>
        <div id="portfolioStats" style="font-size:13px;line-height:2;color:var(--text-sub)"></div>
      </div>
    </div>
    <div class="portfolio-card">
      <div class="metric-label">市场状态时间线（近 60 日）</div>
      <canvas id="regimeTimelineChart" style="max-height:200px"></canvas>
    </div>
  </div>

  <!-- 选股结果面板 -->
  <div class="panel" id="panel-scanner">
    <div class="strategy-tabs" id="strategyTabs"></div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px">
      <div class="metric-label">选股结果</div>
      <div class="table-wrap"><table id="resultTable"><thead id="resultHead"></thead><tbody id="resultBody"></tbody></table></div>
      <div class="loading" id="tableLoading"><span class="spinner"></span>加载中...</div>
    </div>
    <div class="portfolio-card">
      <div class="metric-label">板块热力图（Top 30）</div>
      <canvas id="sectorHeatmapChart" style="max-height:400px"></canvas>
    </div>
    <div class="portfolio-card" id="klineSection" style="display:none">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <div class="metric-label" style="margin:0">K线图 — <span id="klineTitle">--</span></div>
        <select id="klineDays" style="padding:3px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:12px">
          <option value="60">60日</option>
          <option value="120" selected>120日</option>
          <option value="250">250日</option>
        </select>
      </div>
      <div style="position:relative;height:300px">
        <canvas id="klineChart"></canvas>
      </div>
    </div>
    <div class="portfolio-grid">
      <div class="portfolio-card"><div class="metric-label">技术信号分布</div><canvas id="signalChart"></canvas></div>
      <div class="portfolio-card"><div class="metric-label">综合评分对比</div><canvas id="factorChart"></canvas></div>
    </div>
  </div>

  <!-- 风控中心面板 -->
  <div class="panel" id="panel-risk">
    <div class="metric-label" style="margin:0 0 8px">风险暴露仪表盘</div>
    <div id="riskExposureSection" style="display:none"></div>
    <div class="risk-grid" id="riskAlerts"></div>
    <div class="metric-label" style="margin:12px 0 8px">压力测试</div>
    <div class="stress-grid" id="stressTests"></div>
    <div class="metric-label" style="margin:16px 0 8px">风控建议</div>
    <div class="portfolio-card" id="riskRecommendation" style="font-size:14px;padding:16px"></div>
  </div>

  <!-- 持仓概况面板 -->
  <div class="panel" id="panel-positions">
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">持仓概况 <button id="exportPositionsBtn" class="secondary" style="margin-left:8px;font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer">导出 CSV</button><button id="exportPositionsExcelBtn" class="secondary" style="margin-left:4px;font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer">导出 Excel</button></div>
        <div class="table-wrap"><table id="posTable"><thead><tr><th>代码</th><th>名称</th><th>行业</th><th>持仓</th><th>成本</th><th>现价</th><th>盈亏</th><th>仓位</th><th>止损</th><th>止盈</th></tr></thead><tbody id="posBody"></tbody></table></div>
        <div class="loading" id="posLoading"><span class="spinner"></span>加载中...</div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">持仓汇总</div>
        <div id="posSummary" style="font-size:13px;line-height:2;color:var(--text-sub)"></div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">实时盈亏</div>
        <div id="realtimePnL" style="margin-top:12px;font-size:13px">加载中...</div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">持仓分布（按行业）</div>
        <canvas id="positionDistChart" style="max-height:250px"></canvas>
      </div>
    </div>
    <div id="positionDetail" style="display:none"></div>
    <div class="portfolio-card">
      <div class="metric-label">风控预警</div>
      <div id="posAlerts" style="font-size:13px;color:var(--text-sub)"></div>
    </div>
  </div>

  <!-- 因子暴露面板 -->
  <div class="panel" id="panel-exposures">
    <div class="portfolio-grid">
      <div class="portfolio-card"><div class="metric-label">组合因子暴露（加权平均）</div><canvas id="exposureChart"></canvas></div>
      <div class="portfolio-card">
        <div class="metric-label">因子暴露详情</div>
        <div class="table-wrap"><table id="exposureTable"><thead><tr><th>因子</th><th>暴露值</th><th>方向</th></tr></thead><tbody id="exposureBody"></tbody></table></div>
      </div>
    </div>
  </div>

  <!-- 因子评估面板 -->
  <div class="panel" id="panel-eval">
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">因子 IC 分析</div>
        <div class="table-wrap"><table id="evalIcTable"><thead><tr><th>因子</th><th>IC</th><th>Rank IC</th><th>P-Value</th></tr></thead><tbody id="evalIcBody"></tbody></table></div>
        <div class="loading" id="evalLoading"><span class="spinner"></span>加载中...</div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">高相关性因子对</div>
        <div class="table-wrap"><table id="evalCorrTable"><thead><tr><th>因子A</th><th>因子B</th><th>相关性</th></tr></thead><tbody id="evalCorrBody"></tbody></table></div>
        <div id="evalInfo" style="margin-top:8px;font-size:12px;color:var(--text-dim)"></div>
      </div>
    </div>
    <div class="portfolio-card">
      <div class="metric-label">IC 因子权重分布</div>
      <canvas id="weightRadarChart" style="max-height:300px"></canvas>
    </div>
    <div class="portfolio-card">
      <div class="metric-label">因子散点图（volume_ratio vs adx）</div>
      <canvas id="factorScatterChart" style="max-height:300px"></canvas>
    </div>
    <div class="portfolio-card">
      <div class="metric-label">因子评估说明</div>
      <div style="font-size:12px;color:var(--text-sub);line-height:1.6">
        <p>IC (Information Coefficient): 因子值与未来收益的秩相关系数。正值 = 因子有效预测上涨。</p>
        <p>|IC| &gt; 0.05 = 有预测力；高相关性对 (&gt;0.7) 建议考虑合并或剔除冗余因子。</p>
      </div>
    </div>
  </div>

  <!-- 决策审计面板 -->
  <div class="panel" id="panel-audit">
    <div class="metric-label" style="margin-bottom:10px">扫描决策历史</div>
    <div id="auditList"><div class="loading"><span class="spinner"></span>加载中...</div></div>
  </div>

  <!-- 策略回测面板 -->
  <div class="panel" id="panel-backtest">
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">回测参数</div>
        <div style="margin-bottom:10px">
          <label style="font-size:12px;color:var(--text-sub)">股票代码（逗号分隔）</label>
          <input id="btSymbols" type="text" value="000001,600519,000858">
        </div>
        <div style="display:flex;gap:12px;margin-bottom:10px">
          <div style="flex:1"><label style="font-size:12px;color:var(--text-sub)">回测天数</label><input id="btDays" type="number" value="250" min="60" max="1000"></div>
          <div style="flex:1"><label style="font-size:12px;color:var(--text-sub)">止损ATR倍数</label><input id="btSL" type="number" value="2.0" step="0.5"></div>
          <div style="flex:1"><label style="font-size:12px;color:var(--text-sub)">止盈ATR倍数</label><input id="btTP" type="number" value="4.0" step="0.5"></div>
        </div>
        <div style="display:flex;gap:10px">
          <button id="btRunBtn" class="primary" style="background:var(--accent);border:none;border-radius:8px;padding:8px 24px;color:#fff;font-size:13px;font-weight:500;cursor:pointer">运行回测</button>
          <button id="btCompareBtn" class="secondary" style="background:var(--card);border:1px solid var(--border);border-radius:8px;padding:8px 24px;color:var(--text);font-size:13px;font-weight:500;cursor:pointer">策略对比</button>
        </div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">回测结果</div>
        <div id="btResult" style="font-size:13px;line-height:2;color:var(--text-sub)">点击"运行回测"开始</div>
      </div>
    </div>
    <div class="portfolio-grid" id="btCharts" style="display:none">
      <div class="portfolio-card"><div class="metric-label">收益 vs 基准</div><canvas id="btReturnChart"></canvas></div>
      <div class="portfolio-card"><div class="metric-label">退出原因分布</div><canvas id="btExitChart"></canvas></div>
    </div>
    <div class="portfolio-card" style="display:none" id="btCompareSection">
      <div class="metric-label">多策略净值对比</div>
      <canvas id="btCompareChart" style="max-height:300px"></canvas>
    </div>
    <div class="portfolio-card">
      <div class="metric-label">因子贡献度</div>
      <canvas id="btContribChart" style="max-height:250px"></canvas>
    </div>
    <div class="portfolio-card">
      <div class="metric-label">收益归因瀑布图</div>
      <canvas id="attributionChart" style="max-height:250px"></canvas>
    </div>
  </div>

  <!-- 历史报告面板 -->
  <div class="panel" id="panel-reports">
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">每日策略报告</div>
        <div class="table-wrap">
          <table id="reportsTable">
            <thead><tr><th>日期</th><th>情绪</th><th>候选股</th><th>信号明细</th></tr></thead>
            <tbody id="reportsBody"></tbody>
          </table>
        </div>
        <div class="loading" id="reportsLoading"><span class="spinner"></span>加载中...</div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">推送日志</div>
        <div class="table-wrap">
          <table id="pushLogTable">
            <thead><tr><th>时间</th><th>类型</th><th>状态</th><th>详情</th></tr></thead>
            <tbody id="pushLogBody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- 信号追踪面板 -->
  <div class="panel" id="panel-signals">
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">总信号数</div>
        <div class="metric-value" id="sigTotal">--</div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">准确率</div>
        <div class="metric-value" id="sigAccuracy">--</div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">平均 5 日收益</div>
        <div class="metric-value" id="sigAvgReturn">--</div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">盈亏比</div>
        <div class="metric-value" id="sigPLRatio">--</div>
      </div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px">
      <div class="metric-label">最近信号</div>
      <div class="table-wrap"><table id="signalTable"><thead><tr><th>日期</th><th>代码</th><th>信号</th><th>置信度</th><th>IC分数</th><th>5日收益</th><th>结果</th></tr></thead><tbody id="signalBody"></tbody></table></div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px">
      <div class="metric-label">按来源分组</div>
      <canvas id="signalSourceChart"></canvas>
    </div>
  </div>

  <!-- 交易历史面板 -->
  <div class="panel" id="panel-trades">
    <div style="margin-bottom:12px;display:flex;align-items:center;gap:8px">
      <button id="exportTradesBtn" class="secondary" style="font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer">导出 CSV</button>
      <button id="exportTradesExcelBtn" class="secondary" style="font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer">导出 Excel</button>
      <select id="tradeSortSelect" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:13px">
        <option value="date">按时间</option>
        <option value="pnl">按收益（高→低）</option>
        <option value="pnl_asc">按收益（低→高）</option>
      </select>
    </div>
    <div class="table-wrap">
      <table id="tradeHistoryTable">
        <thead><tr><th>时间</th><th>代码</th><th>操作</th><th>价格</th><th>股数</th><th>盈亏</th><th>持有天数</th><th>原因</th></tr></thead>
        <tbody id="tradeHistoryBody"></tbody>
      </table>
    </div>
  </div>

  <!-- 设置面板 -->
  <div class="panel" id="panel-settings">
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px">
      <h3 style="margin:0 0 12px">默认参数</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:13px">
        <div>
          <label>默认策略</label>
          <select id="settingPreset" style="width:100%;padding:6px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text)">
            <option value="balanced">均衡</option>
            <option value="aggressive">激进</option>
            <option value="conservative">保守</option>
          </select>
        </div>
        <div>
          <label>默认回测天数</label>
          <input type="number" id="settingDays" value="250" min="30" max="1000" style="width:100%;padding:6px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text)">
        </div>
        <div>
          <label>默认股票池</label>
          <input type="text" id="settingSymbols" placeholder="留空使用默认" style="width:100%;padding:6px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text)">
        </div>
      </div>
    </div>

    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px">
      <h3 style="margin:0 0 12px">通知偏好</h3>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px">
        <label><input type="checkbox" id="settingNotifySignals" checked> 信号通知</label>
        <label><input type="checkbox" id="settingNotifyTrades" checked> 交易通知</label>
        <label><input type="checkbox" id="settingNotifyAlerts" checked> 告警通知</label>
        <label><input type="checkbox" id="settingNotifyDaily" checked> 每日报告</label>
      </div>
    </div>

    <div style="text-align:right">
      <button id="saveSettingsBtn" class="primary">保存设置</button>
      <span id="settingsStatus" style="margin-left:8px;font-size:12px;color:var(--text-sub)"></span>
    </div>
  </div>
</div>
            <aside class="right-panel" id="right-panel">
              <div class="panel-section"><div class="panel-title">快捷操作</div><button id="refreshBtn" class="refresh-btn">刷新数据</button><select id="presetSelect" class="preset-select"><option value="balanced">均衡策略</option><option value="aggressive">激进策略</option><option value="conservative">保守策略</option></select></div>
              <div class="panel-section"><div class="panel-title">观察列表</div><div id="watchList">加载中...</div></div>
            </aside>
          </div>
          <footer class="statusbar" id="statusbar"></footer>
        </div>
      </div>
      <div class="modal-overlay" id="stockModal"><div class="modal"><h3 id="stockModalTitle">个股详情</h3><button class="modal-close">&times;</button><div id="stockScorePanel"></div><div id="stockFactorsPanel"></div><div id="stockJudgesPanel"></div></div></div>
      <div class="modal-overlay" id="auditModal"><div class="modal"><h3 id="auditModalTitle">扫描审计</h3><button class="modal-close">&times;</button><div id="auditDetailPanel"></div></div></div>
    `;
  }
}