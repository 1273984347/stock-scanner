/**
 * chartExport.js - 图表截图导出工具
 * 支持 Chart.js、TradingView Lightweight Charts、Canvas
 */

/**
 * 导出 Chart.js 图表为 PNG
 * @param {Chart} chart - Chart.js 实例
 * @param {string} filename - 文件名
 */
export function exportChartJs(chart, filename) {
  if (!chart) return;

  try {
    const base64 = chart.toBase64Image('image/png', 1);
    downloadBase64(base64, filename || 'chart.png');
  } catch (e) {
    console.error('[ChartExport] Chart.js export failed:', e);
    fallbackExport(chart.canvas, filename);
  }
}

/**
 * 导出 TradingView Lightweight Charts 图表为 PNG
 * @param {Object} chart - Lightweight Charts 实例
 * @param {string} filename - 文件名
 */
export function exportTradingViewChart(chart, filename) {
  if (!chart || !chart.takeScreenshot) return;

  try {
    const canvas = chart.takeScreenshot();
    const base64 = canvas.toDataURL('image/png');
    downloadBase64(base64, filename || 'kline.png');
  } catch (e) {
    console.error('[ChartExport] TradingView export failed:', e);
  }
}

/**
 * 导出 Canvas 元素为 PNG
 * @param {HTMLCanvasElement} canvas - Canvas 元素
 * @param {string} filename - 文件名
 */
export function exportCanvas(canvas, filename) {
  if (!canvas) return;

  try {
    const base64 = canvas.toDataURL('image/png');
    downloadBase64(base64, filename || 'canvas.png');
  } catch (e) {
    console.error('[ChartExport] Canvas export failed:', e);
    fallbackExport(canvas, filename);
  }
}

/**
 * 回退导出方法（处理跨域问题）
 * @param {HTMLCanvasElement} canvas - Canvas 元素
 * @param {string} filename - 文件名
 */
function fallbackExport(canvas, filename) {
  try {
    // 创建新 canvas 绘制内容
    const newCanvas = document.createElement('canvas');
    newCanvas.width = canvas.width;
    newCanvas.height = canvas.height;
    const ctx = newCanvas.getContext('2d');
    ctx.drawImage(canvas, 0, 0);

    const base64 = newCanvas.toDataURL('image/png');
    downloadBase64(base64, filename || 'chart.png');
  } catch (e) {
    console.error('[ChartExport] Fallback export failed:', e);
    alert('图表导出失败，请尝试截图工具');
  }
}

/**
 * 下载 Base64 数据为文件
 * @param {string} base64 - Base64 数据
 * @param {string} filename - 文件名
 */
function downloadBase64(base64, filename) {
  const link = document.createElement('a');
  link.href = base64;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

/**
 * 生成文件名
 * @param {string} type - 图表类型
 * @param {string} code - 股票代码（可选）
 * @returns {string}
 */
export function generateFilename(type, code = '') {
  const date = new Date().toISOString().slice(0, 10);
  const parts = [type];
  if (code) parts.push(code);
  parts.push(date);
  return parts.join('_') + '.png';
}

/**
 * 为图表添加导出按钮
 * @param {HTMLElement} container - 图表容器
 * @param {Function} exportFn - 导出函数
 * @param {string} tooltip - 提示文本
 */
export function addExportButton(container, exportFn, tooltip = '导出图片') {
  const btn = document.createElement('button');
  btn.className = 'chart-export-btn';
  btn.title = tooltip;
  btn.setAttribute('aria-label', tooltip);
  btn.textContent = '📷';
  btn.style.cssText = `
    position: absolute;
    top: 4px;
    right: 4px;
    width: 24px;
    height: 24px;
    border: none;
    background: rgba(0,0,0,0.5);
    color: #fff;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    opacity: 0;
    transition: opacity 0.2s;
    z-index: 10;
  `;

  container.style.position = 'relative';
  container.appendChild(btn);

  container.addEventListener('mouseenter', () => { btn.style.opacity = '1'; });
  container.addEventListener('mouseleave', () => { btn.style.opacity = '0'; });

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    exportFn();
  });
}

/**
 * 批量导出所有图表
 * @param {string} prefix - 文件名前缀
 */
export function exportAllCharts(prefix = 'stock-scanner') {
  const canvases = document.querySelectorAll('canvas');
  const date = new Date().toISOString().slice(0, 10);

  canvases.forEach((canvas, index) => {
    const label = canvas.getAttribute('aria-label') || `chart-${index}`;
    const filename = `${prefix}_${label}_${date}.png`;

    setTimeout(() => {
      exportCanvas(canvas, filename);
    }, index * 200); // 错开下载避免浏览器阻止
  });
}
