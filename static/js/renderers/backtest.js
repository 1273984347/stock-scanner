// backtest.js — 保持现有逻辑（用户触发，非状态驱动）
class BacktestRenderer {
  init() {
    const btn = document.getElementById('btRunBtn');
    if (btn) {
      btn.addEventListener('click', () => this._runBacktest());
    }
  }
  async _runBacktest() {
    const btn = document.getElementById('btRunBtn');
    btn.disabled = true; btn.textContent = '回测中...';
    document.getElementById('btResult').innerHTML = '<span class="spinner"></span>正在运行回测，请稍候...';
    const symbols = document.getElementById('btSymbols').value;
    const days = document.getElementById('btDays').value;
    const sl = document.getElementById('btSL').value;
    const tp = document.getElementById('btTP').value;
    try {
      const res = await fetch(`/api/backtest?symbols=${encodeURIComponent(symbols)}&days=${days}&stop_loss_atr=${sl}&take_profit_atr=${tp}`, { signal: AbortSignal.timeout(120000) }).then(r => r.json());
      if (res.error) { document.getElementById('btResult').innerHTML = `<span style="color:var(--down)">${res.error}</span>`; return; }
      const m = res.metrics || {};
      document.getElementById('btResult').innerHTML = `
        <b>回测指标</b><br>
        总收益率: <b class="${(m.total_return||0)>0?'text-up':'text-down'}">${((m.total_return||0)*100).toFixed(2)}%</b><br>
        年化收益: <b>${((m.annual_return||0)*100).toFixed(2)}%</b><br>
        最大回撤: <b class="text-down">${((m.max_drawdown||0)*100).toFixed(2)}%</b><br>
        夏普比率: <b>${(m.sharpe||0).toFixed(2)}</b><br>
        胜率: <b>${((m.win_rate||0)*100).toFixed(1)}%</b><br>
        交易次数: <b>${res.trade_count||0}</b>
      `;
    } catch(e) {
      document.getElementById('btResult').innerHTML = `<span style="color:var(--down)">回测出错: ${e.message}</span>`;
    } finally {
      btn.disabled = false; btn.textContent = '运行回测';
    }
  }
}
export { BacktestRenderer };
