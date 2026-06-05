// lightweight-charts is loaded as a global via <script> tag
const { createChart } = window.LightweightCharts || {};

export class ChartPanel {
  constructor(container) {
    this.container = container;
    this.chart = null;
    this.candleSeries = null;
    this.volumeSeries = null;
    this.init();
  }

  init() {
    this.chart = createChart(this.container, {
      layout: {
        background: { color: '#1A1A2E' },
        textColor: '#787B86',
      },
      grid: {
        vertLines: { color: 'rgba(42, 46, 57, 0.4)' },
        horzLines: { color: 'rgba(42, 46, 57, 0.4)' },
      },
      crosshair: {
        mode: 0,
      },
      rightPriceScale: {
        borderColor: 'rgba(42, 46, 57, 0.4)',
      },
      timeScale: {
        borderColor: 'rgba(42, 46, 57, 0.4)',
        timeVisible: true,
      },
    });

    this.candleSeries = this.chart.addCandlestickSeries({
      upColor: '#26A69A',
      downColor: '#EF5350',
      borderUpColor: '#26A69A',
      borderDownColor: '#EF5350',
      wickUpColor: '#26A69A',
      wickDownColor: '#EF5350',
    });

    this.volumeSeries = this.chart.addHistogramSeries({
      color: '#26a69a',
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });

    // Handle resize
    const observer = new ResizeObserver(() => {
      this.chart.applyOptions({
        width: this.container.clientWidth,
        height: this.container.clientHeight,
      });
    });
    observer.observe(this.container);
  }

  setData(data) {
    if (!data || !data.length) return;

    const candleData = data.map(d => ({
      time: d.time,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    const volumeData = data.map(d => ({
      time: d.time,
      value: d.volume,
      color: d.close >= d.open ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
    }));

    this.candleSeries.setData(candleData);
    this.volumeSeries.setData(volumeData);
    this.chart.timeScale().fitContent();
  }

  updateLastBar(bar) {
    this.candleSeries.update(bar);
    this.volumeSeries.update({
      time: bar.time,
      value: bar.volume,
      color: bar.close >= bar.open ? 'rgba(38,166,154,0.5)' : 'rgba(239,83,80,0.5)',
    });
  }

  destroy() {
    if (this.chart) {
      this.chart.remove();
      this.chart = null;
    }
  }
}
