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
          <!-- E 方案 · 大标题区（hero） -->
          <div class="hero" id="hero">
            <h1 id="heroTitle">盘后复盘</h1>
            <div class="sub" id="heroSub">加载中…</div>
            <div class="hero-row" id="heroStats"></div>
          </div>
          <div class="content-area">
            <div class="chart-area" id="chart-area">
              <!-- E 方案 · 观察列表卡片网格 -->
              <div class="grid" id="watchGrid"></div>

              <!-- 14 个功能面板（id 不变，renderer 依赖） -->
              <div class="panels-container">
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
        <div class="metric-label">实时资金曲线</div>
        <canvas id="equityCurveChart"></canvas>
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
      <div id="klineChart" style="height:300px"></div>
      <div id="klineRangeContainer"></div>
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
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px">
      <div class="metric-label">持仓相关性矩阵</div>
      <canvas id="correlationHeatmap" style="height:250px"></canvas>
    </div>
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
        <div id="posPagination" style="display:flex;justify-content:center;padding:8px 0"></div>
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
        <div class="metric-label">因子 IC 时间序列</div>
        <canvas id="icHistoryChart"></canvas>
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

  <!-- 通知中心面板（P26：信号/交易/异常通知的历史记录；消费 /api/notifications + /api/notifications/stats） -->
  <div class="panel" id="panel-notifications">
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px 14px;margin-bottom:12px;display:flex;gap:16px;flex-wrap:wrap;align-items:center">
      <div class="metric-label" style="margin:0">通知统计</div>
      <span style="font-size:13px">总计 <b id="notifStatTotal">--</b></span>
      <span id="notifStatByType" style="font-size:12px;color:var(--text-dim)"></span>
      <span style="flex:1"></span>
      <select id="notifTypeSelect" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:13px">
        <option value="">全部类型</option>
        <option value="signal">信号</option>
        <option value="trade">交易</option>
        <option value="alert">警报</option>
        <option value="system">系统</option>
      </select>
      <input id="notifStartDate" type="date" title="开始日期" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:13px">
      <input id="notifEndDate" type="date" title="结束日期" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:13px">
      <input id="notifKeyword" type="search" placeholder="关键词搜索" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:13px;min-width:140px">
      <button id="notifRefreshBtn" class="secondary" style="font-size:12px;padding:2px 8px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer">刷新</button>
    </div>
    <div class="table-wrap">
      <table id="notificationsTable">
        <thead><tr><th>时间</th><th>类型</th><th>级别</th><th>标题</th><th>内容</th></tr></thead>
        <tbody id="notificationsBody"></tbody>
      </table>
    </div>
    <div class="loading" id="notificationsLoading"><span class="spinner"></span>加载中...</div>
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
      <div id="signalPagination" style="display:flex;justify-content:center;padding:8px 0"></div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px">
      <div class="metric-label">按来源分组</div>
      <canvas id="signalSourceChart"></canvas>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-top:12px">
      <div class="metric-label">信号准确率趋势</div>
      <canvas id="accuracyTrendChart"></canvas>
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
    <div id="tradePagination" style="display:flex;justify-content:center;padding:8px 0"></div>
  </div>

  <!-- 策略对比面板 -->
  <div class="panel" id="panel-strategy">
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-bottom:12px">
      <div class="metric-label">多策略对比</div>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center">
        <input id="scSymbols" placeholder="股票代码(逗号分隔)" value="600519,000858" style="padding:6px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text);font-size:13px;flex:1;min-width:200px">
        <label style="font-size:12px;color:var(--text-dim)">止损(ATR): <input id="scSL" type="range" min="1" max="5" step="0.5" value="2" style="width:80px;vertical-align:middle"> <span id="scSLVal">2</span></label>
        <label style="font-size:12px;color:var(--text-dim)">止盈(ATR): <input id="scTP" type="range" min="1" max="8" step="0.5" value="3" style="width:80px;vertical-align:middle"> <span id="scTPVal">3</span></label>
        <label style="font-size:12px;color:var(--text-dim)">天数: <input id="scDays" type="range" min="60" max="500" step="10" value="250" style="width:80px;vertical-align:middle"> <span id="scDaysVal">250</span></label>
        <button id="scCompareBtn" style="padding:6px 16px;border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;cursor:pointer;font-size:13px">运行对比</button>
        <button id="scRecommendBtn" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);cursor:pointer;font-size:13px">参数推荐</button>
        <button id="scHistoryBtn" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);cursor:pointer;font-size:13px">历史记录</button>
      </div>
      <canvas id="scEquityChart" style="height:300px"></canvas>
    </div>
    <div class="portfolio-grid">
      <div class="portfolio-card">
        <div class="metric-label">策略统计</div>
        <div class="table-wrap"><table><thead><tr><th>策略</th><th>夏普</th><th>最大回撤</th><th>胜率</th><th>总收益</th><th>交易数</th></tr></thead><tbody id="scStatsBody"></tbody></table></div>
      </div>
      <div class="portfolio-card">
        <div class="metric-label">参数推荐</div>
        <div id="scRecommendResult" style="font-size:13px;color:var(--text-sub)">点击"参数推荐"获取最优参数</div>
      </div>
    </div>
    <div style="background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px;margin-top:12px">
      <div class="metric-label">回测历史</div>
      <div class="table-wrap"><table><thead><tr><th>时间</th><th>股票</th><th>最佳夏普</th><th>策略</th></tr></thead><tbody id="scHistoryBody"></tbody></table></div>
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
        <div>
          <label>字体大小</label>
          <select id="settingFontSize" style="width:100%;padding:6px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text)">
            <option value="12">12px - 小</option>
            <option value="14" selected>14px - 标准</option>
            <option value="16">16px - 中</option>
            <option value="18">18px - 大</option>
            <option value="20">20px - 特大</option>
          </select>
        </div>
        <div>
          <label>语言 / Language</label>
          <select id="settingLocale" style="width:100%;padding:6px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text)">
            <option value="zh">中文</option>
            <option value="en">English</option>
          </select>
        </div>
        <div>
          <label>布局方向</label>
          <select id="settingDirection" style="width:100%;padding:6px;border-radius:4px;border:1px solid var(--border);background:var(--card);color:var(--text)">
            <option value="ltr">从左到右 (LTR)</option>
            <option value="rtl">从右到左 (RTL)</option>
          </select>
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
</
              </div>
            </div>
            </div>
            <!-- E 方案 · 右侧检查器 -->
            <aside class="inspector" id="right-panel">
              <div class="ins-block" id="inspectorHead">
                <div class="ins-k">当前标的</div>
                <div class="ins-name" id="symbolTicker">全市场扫描</div>
                <div class="ins-code" id="symbolName">A股 5,600+ 只</div>
              </div>
              <div class="ins-block">
                <div class="ins-k">核心读数</div>
                <div class="ins-row"><span class="k">候选</span><span class="v" id="totalCand">--</span></div>
                <div class="ins-row"><span class="k">情绪</span><span class="v" id="sentiment">--</span></div>
                <div class="ins-row"><span class="k">倾向</span><span class="v" id="sentimentLabel">--</span></div>
                <div class="ins-row"><span class="k">门控</span><span class="v" id="macroState">--</span></div>
                <div class="ins-row"><span class="k">依据</span><span class="v" id="macroLabel">--</span></div>
                <div class="ins-row"><span class="k">仓位</span><span class="v" id="exposure">--</span></div>
                <div class="ins-row"><span class="k">乘数</span><span class="v" id="exposureLabel">--</span></div>
                <div class="ins-row"><span class="k">夏普</span><span class="v" id="sharpe">--</span></div>
                <div class="ins-row"><span class="k">持仓</span><span class="v" id="nPositions">--</span></div>
                <div class="ins-row"><span class="k">涨跌</span><span class="v" id="symbolChange">--</span></div>
              </div>
              <div class="ins-block" id="inspectorFactors">
                <div class="ins-k">评分构成</div>
                <div id="factorBreakdown"></div>
              </div>
              <div class="ins-block">
                <div class="ins-k">快捷操作</div>
                <div id="colorSchemeToggle"></div>
                <button id="refreshBtn" class="refresh-btn">刷新数据</button>
                <select id="presetSelect" class="preset-select"><option value="balanced">均衡策略</option><option value="aggressive">激进策略</option><option value="conservative">保守策略</option></select>
              </div>
              <div class="ins-block">
                <div class="ins-k">观察列表</div>
                <div class="watch-head">
                  <span class="watch-col-code">代码</span><span class="watch-col-name">名称</span>
                  <span class="watch-col-last">最新</span><span class="watch-col-chg">涨跌</span>
                </div>
                <div id="watchList">加载中...</div>
              </div>
            </aside>
          </div>
          <footer class="statusbar" id="statusbar"></footer>
        </div>
      </div>
      <div class="modal-overlay" id="stockModal"><div class="modal"><h3 id="stockModalTitle">个股详情</h3><button class="modal-close">&times;</button><div id="stockScorePanel"></div><div id="stockFactorsPanel"></div><div id="stockJudgesPanel"></div></div></div>
      <div class="modal-overlay" id="auditModal"><div class="modal"><h3 id="auditModalTitle">扫描审计</h3><button class="modal-close">&times;</button><div id="auditDetailPanel"></div></div></div>
    `;

    this._splitIntoPanes();
  }

  /**
   * TradingView 式窗格拆分 —— 真正的结构重排（不只是换皮）
   *
   * TradingView 的工作区是「主图 + 副图」上下窗格，而非单列长滚动。
   * 本方法把每个面板的子块按内容自动归类：
   *   · 含 canvas 且不含 table  -> 上窗格（图表区）
   *   · 含 table  且不含 canvas -> 下窗格（数据区）
   *   · 两者兼有或皆无          -> 留在主窗格（保持原序）
   *
   * 只搬运 DOM 节点，**不改任何 id / 类名** => renderer 按 id 取元素逻辑零影响。
   */
  _splitIntoPanes() {
    document.querySelectorAll('.panels-container .panel').forEach((panel) => {
      const children = Array.from(panel.children);
      if (children.length === 0) return;

      const charts = [];
      const tables = [];
      const mixed = [];

      children.forEach((child) => {
        const hasCanvas = child.tagName === 'CANVAS' || !!child.querySelector('canvas');
        const hasTable = child.tagName === 'TABLE' || !!child.querySelector('table');
        if (hasCanvas && hasTable) {
          // 混合容器（如 portfolio-grid）：拆开其直接子元素再分派，
          // 使纯表格进数据窗格、纯图表进图表窗格
          const subs = Array.from(child.children);
          let split = false;
          subs.forEach(sub => {
            const subCanvas = sub.tagName === 'CANVAS' || !!sub.querySelector('canvas');
            const subTable = sub.tagName === 'TABLE' || !!sub.querySelector('table');
            if (subCanvas && !subTable) { charts.push(sub); split = true; }
            else if (subTable && !subCanvas) { tables.push(sub); split = true; }
          });
          if (split) {
            // 未被拆走的子元素保持原序留在混合区
            subs.forEach(sub => {
              const subCanvas = sub.tagName === 'CANVAS' || !!sub.querySelector('canvas');
              const subTable = sub.tagName === 'TABLE' || !!sub.querySelector('table');
              if ((subCanvas && !subTable) || (subTable && !subCanvas)) return;
              mixed.push(sub);
            });
            return;
          }
          mixed.push(child);
        }
        else if (hasCanvas) charts.push(child);
        else if (hasTable) tables.push(child);
        else mixed.push(child);
      });

      if (charts.length === 0 && tables.length === 0) return;

      const wrap = document.createElement('div');
      wrap.className = 'pane-split';

      // 窗格标题承载真实信息：取该窗格内子块的标题（而非泛泛的「图表/数据」）
      const deriveTitle = (nodes, fallback) => {
        const titles = nodes
          .map(n => (n.querySelector ? n.querySelector('.metric-label') : null))
          .filter(Boolean)
          .map(e => e.textContent.trim())
          .filter(Boolean);
        if (titles.length === 0) return fallback;
        const uniq = Array.from(new Set(titles));
        return uniq.length <= 2 ? uniq.join(' · ') : uniq.slice(0, 2).join(' · ') + ' +' + (uniq.length - 2);
      };

      const makePane = (cls, title) => {
        const pane = document.createElement('section');
        pane.className = 'pane ' + cls;
        const head = document.createElement('header');
        head.className = 'pane-header';
        const t = document.createElement('span');
        t.className = 'pane-title';
        t.textContent = title;
        head.appendChild(t);
        const body = document.createElement('div');
        body.className = 'pane-body';
        pane.appendChild(head);
        pane.appendChild(body);
        wrap.appendChild(pane);
        return body;
      };

      let primaryBody = null;
      if (charts.length > 0) primaryBody = makePane('pane-primary', deriveTitle(charts, '可视化'));
      if (tables.length > 0) makePane('pane-secondary', deriveTitle(tables, '明细'));

      if (mixed.length > 0) {
        if (!primaryBody) primaryBody = makePane('pane-primary', deriveTitle(mixed, '概览'));
        mixed.forEach(n => primaryBody.appendChild(n));
      }

      charts.forEach(n => primaryBody.appendChild(n));
      wrap.querySelectorAll('.pane-secondary .pane-body').forEach(body => {
        tables.forEach(n => body.appendChild(n));
      });

      panel.innerHTML = '';
      panel.appendChild(wrap);
    });
  }
}