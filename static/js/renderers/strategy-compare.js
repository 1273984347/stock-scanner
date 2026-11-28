import { apiClient } from '/js/api.js';
import { getThemeColor, seriesColors } from '/js/theme.js';

/**
 * StrategyCompareRenderer — 策略对比界面
 * 多策略净值曲线对比 + 参数调优 + 回测历史 + 参数推荐
 */
class StrategyCompareRenderer {
  constructor() {
    this._charts = {};
    this._init();
  }

  _init() {
    // 对比按钮
    const cmpBtn = document.getElementById('scCompareBtn');
    if (cmpBtn) {
      cmpBtn.addEventListener('click', () => this._runCompare());
    }
    // 推荐按钮
    const recBtn = document.getElementById('scRecommendBtn');
    if (recBtn) {
      recBtn.addEventListener('click', () => this._loadRecommendation());
    }
    // 历史按钮
    const histBtn = document.getElementById('scHistoryBtn');
    if (histBtn) {
      histBtn.addEventListener('click', () => this._loadHistory());
    }
    // 参数滑块
    this._setupSliders();
  }

  _setupSliders() {
    const sliders = [
      { id: 'scSL', label: '止损(ATR)', default: 2 },
      { id: 'scTP', label: '止盈(ATR)', default: 3 },
      { id: 'scDays', label: '回测天数', default: 250 },
    ];
    sliders.forEach(s => {
      const input = document.getElementById(s.id);
      const label = document.getElementById(s.id + 'Val');
      if (input && label) {
        input.addEventListener('input', () => {
          label.textContent = input.value;
        });
      }
    });
  }

  async _runCompare() {
    const btn = document.getElementById('scCompareBtn');
    btn.disabled = true;
    btn.textContent = '对比中...';

    const symbols = document.getElementById('scSymbols')?.value || '600519,000858';
    const days = Math.max(60, Math.min(500, parseInt(document.getElementById('scDays')?.value, 10) || 250));
    const sl = Math.max(1, Math.min(5, parseFloat(document.getElementById('scSL')?.value) || 2));
    const tp = Math.max(1, Math.min(8, parseFloat(document.getElementById('scTP')?.value) || 3));

    try {
      // 运行 3 个预设策略对比
      const presets = ['balanced', 'aggressive', 'conservative'];
      const results = await Promise.all(
        presets.map(preset =>
          fetch(`/api/backtest?symbols=${encodeURIComponent(symbols)}&days=${days}&stop_loss_atr=${sl}&take_profit_atr=${tp}&preset=${preset}`, {
            method: 'POST',
            headers: { 'X-API-Key': sessionStorage.getItem('api_key') || 'dev' },
            signal: AbortSignal.timeout(120000),
          }).then(r => r.json()).catch(() => null)
        )
      );

      this._renderComparison(results, presets);

      // 保存到历史
      await apiClient.post('/api/strategy/save', {
        symbols, days, sl, tp,
        results: results.map((r, i) => ({
          preset: presets[i],
          sharpe: r?.sharpe,
          max_dd: r?.max_drawdown,
          win_rate: r?.win_rate,
          total_return: r?.total_return,
        })),
      });
    } catch (e) {
      console.error('[StrategyCompare] compare failed:', e);
    } finally {
      btn.disabled = false;
      btn.textContent = '运行对比';
    }
  }

  _renderComparison(results, presets) {
    const canvas = document.getElementById('scEquityChart');
    if (!canvas) return;
    if (this._charts.equity) this._charts.equity.destroy();

    const colors = seriesColors(3);
    const datasets = results.map((r, i) => {
      if (!r?.equity_curve) return null;
      return {
        label: presets[i],
        data: r.equity_curve.map((v, j) => ({ x: j, y: v })),
        borderColor: colors[i],
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.1,
      };
    }).filter(Boolean);

    this._charts.equity = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: { datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { position: 'top', labels: { color: getThemeColor('--text-sub') } },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          x: { type: 'linear', title: { display: true, text: '交易日' } },
          y: { title: { display: true, text: '净值' } },
        },
      },
    });

    // 渲染统计表格
    const tbody = document.getElementById('scStatsBody');
    if (tbody) {
      while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
      results.forEach((r, i) => {
        if (!r) return;
        const tr = document.createElement('tr');
        const cells = [
          presets[i],
          (r.sharpe || 0).toFixed(2),
          ((r.max_drawdown || 0) * 100).toFixed(1) + '%',
          ((r.win_rate || 0) * 100).toFixed(1) + '%',
          ((r.total_return || 0) * 100).toFixed(1) + '%',
          String(r.total_trades || 0),
        ];
        cells.forEach(txt => {
          const td = document.createElement('td');
          td.textContent = txt;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }
  }

  async _loadRecommendation() {
    const btn = document.getElementById('scRecommendBtn');
    btn.disabled = true;
    btn.textContent = '计算中...';

    const symbols = document.getElementById('scSymbols')?.value || '600519,000858';
    try {
      const data = await apiClient.get('/api/strategy/recommend', { symbols });
      if (data?.recommended) {
        const r = data.recommended;
        const container = document.getElementById('scRecommendResult');
        if (container) {
          while (container.firstChild) container.removeChild(container.firstChild);
          const div = document.createElement('div');
          div.style.cssText = 'font-size:13px;line-height:1.8';
          const lines = [
            `推荐夏普: ${(r.sharpe || 0).toFixed(2)}`,
            `最大回撤: ${((r.max_dd || 0) * 100).toFixed(1)}%`,
            `胜率: ${((r.win_rate || 0) * 100).toFixed(1)}%`,
            `股票: ${(data.symbols || []).join(', ')}`,
            `说明: ${data.note || ''}`,
          ];
          lines.forEach(line => {
            const p = document.createElement('div');
            p.textContent = line;
            div.appendChild(p);
          });
          container.appendChild(div);
        }
      }
    } catch (e) {
      console.error('[StrategyCompare] recommend failed:', e);
    } finally {
      btn.disabled = false;
      btn.textContent = '参数推荐';
    }
  }

  async _loadHistory() {
    try {
      const data = await apiClient.get('/api/strategy/history', { limit: 20 });
      const tbody = document.getElementById('scHistoryBody');
      if (!tbody || !data?.history) return;
      while (tbody.firstChild) tbody.removeChild(tbody.firstChild);

      data.history.forEach(item => {
        const tr = document.createElement('tr');
        const date = item.id || '--';
        const symbols = item.symbols || '--';
        const results = item.results || [];
        const bestSharpe = results.reduce((max, r) => Math.max(max, r.sharpe || 0), 0);

        [date, symbols, bestSharpe.toFixed(2), results.map(r => r.preset).join('/')].forEach(txt => {
          const td = document.createElement('td');
          td.textContent = txt;
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    } catch (e) {
      console.error('[StrategyCompare] history failed:', e);
    }
  }
}

export { StrategyCompareRenderer };
