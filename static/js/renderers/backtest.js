// backtest.js — 回测可视化（净值曲线 + 交易标记 + 退出分布）
class BacktestRenderer {
  constructor() {
    this._charts = {};
  }

  init() {
    const btn = document.getElementById('btRunBtn');
    if (btn) {
      btn.addEventListener('click', () => this._runBacktest());
    }
  }

  async _runBacktest() {
    const btn = document.getElementById('btRunBtn');
    btn.disabled = true; btn.textContent = '回测中...';
    document.getElementById('btResult').textContent = '正在运行回测，请稍候...';

    const symbols = document.getElementById('btSymbols').value.trim();
    if (!/^\d{6}(,\d{6})*$/.test(symbols)) {
      document.getElementById('btResult').textContent = '股票代码格式错误，请输入6位数字代码，逗号分隔';
      btn.disabled = false; btn.textContent = '运行回测';
      return;
    }

    const days = document.getElementById('btDays').value;
    const sl = document.getElementById('btSL').value;
    const tp = document.getElementById('btTP').value;

    try {
      const res = await fetch(`/api/backtest?symbols=${encodeURIComponent(symbols)}&days=${days}&stop_loss_atr=${sl}&take_profit_atr=${tp}`, {
        method: 'POST',
        headers: { 'X-API-Key': sessionStorage.getItem('api_key') || 'dev' },
        signal: AbortSignal.timeout(120000)
      }).then(r => r.json());

      if (res.error) {
        document.getElementById('btResult').textContent = res.error;
        return;
      }

      this._renderMetrics(res);
      this._renderEquityChart(res.equity_curve || [], res.trades || []);
      this._renderExitChart(res.exit_reasons || {});
      this._renderTradeTable(res.trades || []);

      document.getElementById('btCharts').style.display = 'grid';
    } catch (e) {
      document.getElementById('btResult').textContent = '回测出错: ' + e.message;
    } finally {
      btn.disabled = false; btn.textContent = '运行回测';
    }
  }

  _renderMetrics(res) {
    const m = res.metrics || {};
    const bm = res.benchmark || {};
    const portReturn = (m.total_return || 0) * 100;
    const bmReturn = (bm.total_return || 0) * 100;
    const excess = portReturn - bmReturn;

    const c = document.createElement('div');
    c.style.cssText = 'font-size:13px;line-height:1.8';

    const mk = (label, val, unit, cls) => {
      const d = document.createElement('div');
      const b = document.createElement('b');
      b.textContent = isNaN(val) ? '--' : val.toFixed(2) + unit;
      if (cls === 'up' && val > 0) b.style.color = 'var(--up)';
      else if (cls === 'down' && val < 0) b.style.color = 'var(--down)';
      d.append(label + ': ', b);
      return d;
    };

    const rSpan = (v, cls) => {
      const s = document.createElement('span');
      s.textContent = (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
      s.style.color = cls === 'up' ? 'var(--up)' : cls === 'down' ? 'var(--down)' : 'var(--text)';
      return s;
    };

    const title = document.createElement('b');
    title.textContent = '回测指标';
    c.append(title, document.createElement('br'));

    const row1 = document.createElement('div');
    row1.append('总收益: ', rSpan(portReturn, portReturn >= 0 ? 'up' : 'down'),
      ' | 基准: ', rSpan(bmReturn, ''),
      ' | 超额: ', rSpan(excess, excess >= 0 ? 'up' : 'down'));
    c.append(row1);

    c.append(mk('年化收益', (m.annual_return || 0) * 100, '%'));
    c.append(mk('最大回撤', -(m.max_drawdown || 0) * 100, '%', 'down'));
    c.append(mk('夏普比率', m.sharpe || 0, ''));
    c.append(mk('胜率', (m.win_rate || 0) * 100, '%'));
    c.append(mk('交易次数', res.trade_count || 0, ' 次'));

    if ((res.trade_count || 0) === 0) {
      const note = document.createElement('div');
      note.style.cssText = 'margin-top:8px;padding:6px 10px;background:#f5f5f5;border-radius:4px;font-size:12px;color:#666';
      note.textContent = '无买入信号：当前市场无满足条件的看涨股票，策略空仓等待';
      c.append(note);
    }

    document.getElementById('btResult').textContent = '';
    document.getElementById('btResult').appendChild(c);
  }

  _renderEquityChart(curve, trades) {
    const canvas = document.getElementById('btReturnChart');
    if (!canvas || curve.length === 0) return;

    if (this._charts.equity) this._charts.equity.destroy();

    const labels = curve.map(p => p.date);
    const nav = curve.map(p => p.nav || 1.0);
    const bm = curve.map(p => p.benchmark || 1.0);

    let peak = 0;
    const drawdown = nav.map(v => {
      peak = Math.max(peak, v);
      return ((v - peak) / peak) * 100;
    });

    // Build buy/sell marker arrays
    const buyDates = new Set();
    const sellDates = new Set();
    const buyPoints = [];
    const sellPoints = [];

    (trades || []).forEach(t => {
      if (t.entry_date && !buyDates.has(t.entry_date)) {
        buyDates.add(t.entry_date);
        const idx = curve.findIndex(p => p.date === t.entry_date);
        if (idx >= 0) {
          buyPoints.push({ x: t.entry_date, y: curve[idx].nav || 1.0 });
        }
      }
      if (t.exit_date && !sellDates.has(t.exit_date)) {
        sellDates.add(t.exit_date);
        const idx = curve.findIndex(p => p.date === t.exit_date);
        if (idx >= 0) {
          sellPoints.push({ x: t.exit_date, y: curve[idx].nav || 1.0 });
        }
      }
    });

    this._charts.equity = new Chart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: '策略净值',
            data: nav,
            borderColor: '#4f8cff',
            backgroundColor: 'rgba(79,140,255,0.08)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
          },
          {
            label: '基准(沪深300)',
            data: bm,
            borderColor: '#aaa',
            borderDash: [4, 4],
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 1.5,
          },
          {
            label: '回撤(%)',
            data: drawdown,
            borderColor: 'rgba(220,80,80,0.5)',
            backgroundColor: 'rgba(220,80,80,0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 1,
            yAxisID: 'dd',
          },
          {
            label: '买入',
            data: buyPoints,
            type: 'scatter',
            pointStyle: 'triangle',
            pointRadius: 6,
            pointBackgroundColor: '#50c878',
            pointBorderColor: '#50c878',
            showLine: false,
          },
          {
            label: '卖出',
            data: sellPoints,
            type: 'scatter',
            pointStyle: 'triangle',
            pointRadius: 6,
            pointBackgroundColor: '#dc5050',
            pointBorderColor: '#dc5050',
            pointRotation: 180,
            showLine: false,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'top', labels: { font: { size: 11 } } },
          tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(4) } },
        },
        scales: {
          x: { display: true, ticks: { maxTicksLimit: 10, font: { size: 10 } } },
          y: { position: 'left', title: { display: true, text: '净值' } },
          dd: { position: 'right', title: { display: true, text: '回撤(%)' }, grid: { drawOnChartArea: false } },
        },
      },
    });
  }

  _renderExitChart(reasons) {
    const canvas = document.getElementById('btExitChart');
    if (!canvas) return;

    if (this._charts.exit) this._charts.exit.destroy();

    const labels = Object.keys(reasons);
    const data = Object.values(reasons);

    if (labels.length === 0) return;

    const labelMap = { stop_loss: '止损', take_profit: '止盈', trailing_stop: '移动止损', ema_exit: 'EMA退出', timeout: '超时', partial_exit: '分仓止盈', other: '其他' };
    const colorMap = { stop_loss: '#dc5050', take_profit: '#50c878', trailing_stop: '#ff9800', ema_exit: '#9c27b0', timeout: '#607d8b', partial_exit: '#00bcd4', other: '#795548' };

    this._charts.exit = new Chart(canvas, {
      type: 'doughnut',
      data: {
        labels: labels.map(l => labelMap[l] || l),
        datasets: [{
          data,
          backgroundColor: labels.map(l => colorMap[l] || '#999'),
        }],
      },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'right', labels: { font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: ctx => {
                const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
                const pct = ((ctx.parsed / total) * 100).toFixed(1);
                return ctx.label + ': ' + ctx.parsed + ' 次 (' + pct + '%)';
              },
            },
          },
        },
      },
    });
  }

  _renderTradeTable(trades) {
    const container = document.getElementById('btResult');
    if (!trades || trades.length === 0) return;

    const wrapper = document.createElement('details');
    wrapper.style.cssText = 'margin-top:12px';
    const summary = document.createElement('summary');
    summary.textContent = '交易记录（最近 ' + Math.min(trades.length, 50) + ' 笔）';
    summary.style.cssText = 'cursor:pointer;font-size:12px;color:var(--text-sub)';
    wrapper.appendChild(summary);

    const tbl = document.createElement('table');
    tbl.style.cssText = 'width:100%;font-size:11px;margin-top:6px;border-collapse:collapse';

    const thead = document.createElement('thead');
    const hrow = document.createElement('tr');
    ['代码', '入场', '入场价', '出场', '出场价', '收益%', '天数', '原因'].forEach(h => {
      const th = document.createElement('th');
      th.textContent = h;
      th.style.cssText = 'padding:4px 6px;text-align:left;border-bottom:1px solid var(--border)';
      hrow.appendChild(th);
    });
    thead.appendChild(hrow);
    tbl.appendChild(thead);

    const body = document.createElement('tbody');
    trades.slice(-50).forEach(t => {
      const pnl = (t.pnl_pct || 0) * 100;
      const tr = document.createElement('tr');
      tr.style.color = pnl >= 0 ? 'var(--up)' : 'var(--down)';

      const vals = [
        t.code || '',
        t.entry_date || '',
        (t.entry_price || 0).toFixed(2),
        t.exit_date || '',
        (t.exit_price || 0).toFixed(2),
        pnl.toFixed(2) + '%',
        String(t.hold_days || 0),
        t.exit_reason || '',
      ];
      vals.forEach(v => {
        const td = document.createElement('td');
        td.textContent = v;
        td.style.cssText = 'padding:4px 6px;border-bottom:1px solid var(--border)';
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    tbl.appendChild(body);
    wrapper.appendChild(tbl);
    container.appendChild(wrapper);
  }
}
export { BacktestRenderer };
