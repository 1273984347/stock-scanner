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
      const res = await fetch(`/api/backtest?symbols=${encodeURIComponent(symbols)}&days=${days}&stop_loss_atr=${sl}&take_profit_atr=${tp}`, {
        method: 'POST',
        headers: { 'X-API-Key': localStorage.getItem('api_key') || 'dev' },
        signal: AbortSignal.timeout(120000)
      }).then(r => r.json());
      if (res.error) { document.getElementById('btResult').innerHTML = `<span style="color:var(--down)">${res.error}</span>`; return; }
      const m = res.metrics || {};
      const bm = res.benchmark || {};
      const bmReturn = (bm.total_return || 0) * 100;
      const portReturn = (m.total_return || 0) * 100;
      const excessReturn = portReturn - bmReturn;
      const addField = (label, val, unit, upClass) => {
        const el = document.createElement('div');
        const b = document.createElement('b');
        b.textContent = isNaN(val) ? '--' : val.toFixed(2) + unit;
        if (upClass === 'up' && val > 0) b.style.color = 'var(--up)';
        else if (upClass === 'down' && val < 0) b.style.color = 'var(--down)';
        el.appendChild(document.createTextNode(label + ': '));
        el.appendChild(b);
        return el;
      };
      const container = document.createElement('div');
      container.style.cssText = 'font-size:13px;line-height:1.8';
      const title = document.createElement('b');
      title.textContent = '回测指标';
      container.appendChild(title);
      container.appendChild(document.createElement('br'));
      // 总收益 + 基准 + 超额
      const row1 = document.createElement('div');
      const r1 = (v, cls) => { const s = document.createElement('span'); s.textContent = (v>=0?'+':'')+v.toFixed(2)+'%'; s.style.color = cls==='up'?'var(--up)':cls==='down'?'var(--down)':'var(--text)'; return s; };
      row1.textContent = '总收益率: ';
      row1.appendChild(r1(portReturn, portReturn>=0?'up':'down'));
      row1.appendChild(document.createTextNode(' | 基准: '));
      row1.appendChild(r1(bmReturn, ''));
      row1.appendChild(document.createTextNode(' | 超额: '));
      row1.appendChild(r1(excessReturn, excessReturn>=0?'up':'down'));
      container.appendChild(row1);
      container.appendChild(addField('年化收益', (m.annual_return||0)*100, '%'));
      container.appendChild(addField('最大回撤', -(m.max_drawdown||0)*100, '%', 'down'));
      container.appendChild(addField('夏普比率', m.sharpe||0, ''));
      container.appendChild(addField('胜率', (m.win_rate||0)*100, '%'));
      container.appendChild(addField('交易次数', res.trade_count||0, ' 次'));

      // 0交易时说明原因
      if ((res.trade_count||0) === 0) {
        const note = document.createElement('div');
        note.style.cssText = 'margin-top:8px;padding:6px 10px;background:#f5f5f5;border-radius:4px;font-size:12px;color:#666';
        note.textContent = '💡 无买入信号：当前市场无满足条件的看涨股票（signal=bullish 且 confidence≥0.3），策略空仓等待';
        container.appendChild(note);
      }

      document.getElementById('btResult').innerHTML = '';
      document.getElementById('btResult').appendChild(container);
    } catch(e) {
      document.getElementById('btResult').innerHTML = `<span style="color:var(--down)">回测出错: ${e.message}</span>`;
    } finally {
      btn.disabled = false; btn.textContent = '运行回测';
    }
  }
}
export { BacktestRenderer };
