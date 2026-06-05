/**
 * Treemap.js - 树状图组件
 * 使用 Canvas 绘制 squarified treemap
 */

export class Treemap {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.data = [];
    this.hoveredNode = null;
    this.options = {
      padding: 2,
      borderWidth: 1,
      borderColor: 'rgba(255,255,255,0.2)',
      fontSize: 11,
      fontFamily: 'Inter, sans-serif',
      colors: {
        positive: '#00c853',
        negative: '#ff1744',
        neutral: '#787B86',
        background: '#1a1f28',
        text: '#e1e8f0',
        textDim: '#8494a7'
      },
      ...options
    };

    this._setupCanvas();
    this._bindEvents();
  }

  _setupCanvas() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    this.canvas.width = rect.width * dpr;
    this.canvas.height = rect.height * dpr;
    this.ctx.scale(dpr, dpr);
    this.width = rect.width;
    this.height = rect.height;
  }

  _bindEvents() {
    this.canvas.addEventListener('mousemove', (e) => this._onMouseMove(e));
    this.canvas.addEventListener('mouseleave', () => this._onMouseLeave());
    this.canvas.addEventListener('click', (e) => this._onClick(e));
    window.addEventListener('resize', () => {
      this._setupCanvas();
      this.render(this.data);
    });
  }

  _onMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const node = this._findNode(x, y);
    if (node !== this.hoveredNode) {
      this.hoveredNode = node;
      this.canvas.style.cursor = node ? 'pointer' : 'default';
      this.render(this.data);
    }
  }

  _onMouseLeave() {
    if (this.hoveredNode) {
      this.hoveredNode = null;
      this.canvas.style.cursor = 'default';
      this.render(this.data);
    }
  }

  _onClick(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const node = this._findNode(x, y);
    if (node && this.onClick) {
      this.onClick(node);
    }
  }

  _findNode(x, y) {
    for (const node of this.data) {
      if (x >= node.x && x <= node.x + node.w &&
          y >= node.y && y <= node.y + node.h) {
        return node;
      }
    }
    return null;
  }

  setData(data) {
    this.data = this._processData(data);
    this.render(this.data);
  }

  _processData(data) {
    if (!data || data.length === 0) return [];

    const total = data.reduce((sum, d) => sum + Math.abs(d.value), 0);
    const nodes = data.map(d => ({
      ...d,
      normalizedValue: Math.abs(d.value) / total
    }));

    return this._squarify(nodes, { x: 0, y: 0, w: this.width, h: this.height });
  }

  _squarify(nodes, rect) {
    if (nodes.length === 0) return [];
    if (nodes.length === 1) {
      return [{ ...nodes[0], ...rect }];
    }

    // Sort by value descending
    nodes.sort((a, b) => b.normalizedValue - a.normalizedValue);

    const result = [];
    let remaining = [...nodes];
    let currentRect = { ...rect };

    while (remaining.length > 0) {
      const { row, rest } = this._layoutRow(remaining, currentRect);
      result.push(...row);
      remaining = rest;

      // Update remaining rect
      if (currentRect.w >= currentRect.h) {
        const rowWidth = row.reduce((sum, n) => sum + n.normalizedValue, 0) / this._sumValues(remaining.concat(row));
        currentRect = {
          x: currentRect.x + currentRect.w * rowWidth,
          y: currentRect.y,
          w: currentRect.w * (1 - rowWidth),
          h: currentRect.h
        };
      } else {
        const rowHeight = row.reduce((sum, n) => sum + n.normalizedValue, 0) / this._sumValues(remaining.concat(row));
        currentRect = {
          x: currentRect.x,
          y: currentRect.y + currentRect.h * rowHeight,
          w: currentRect.w,
          h: currentRect.h * (1 - rowHeight)
        };
      }
    }

    return result;
  }

  _layoutRow(nodes, rect) {
    const isWide = rect.w >= rect.h;
    const row = [];
    const rest = [];
    let sum = 0;

    for (const node of nodes) {
      const newSum = sum + node.normalizedValue;
      const aspect = this._worstAspectRatio([...row, { normalizedValue: newSum }], rect);

      if (row.length > 0 && aspect > this._worstAspectRatio(row, rect)) {
        rest.push(node);
      } else {
        row.push(node);
        sum = newSum;
      }
    }

    // Position nodes in row
    let offset = 0;
    const total = row.reduce((s, n) => s + n.normalizedValue, 0);

    for (const node of row) {
      const ratio = node.normalizedValue / total;
      if (isWide) {
        node.x = rect.x;
        node.y = rect.y + offset * rect.h;
        node.w = rect.w;
        node.h = rect.h * ratio;
      } else {
        node.x = rect.x + offset * rect.w;
        node.y = rect.y;
        node.w = rect.w * ratio;
        node.h = rect.h;
      }
      offset += ratio;
    }

    return { row, rest };
  }

  _worstAspectRatio(row, rect) {
    const total = row.reduce((s, n) => s + n.normalizedValue, 0);
    const isWide = rect.w >= rect.h;
    const scale = isWide ? rect.h / rect.w : rect.w / rect.h;

    let worst = 0;
    for (const node of row) {
      const ratio = node.normalizedValue / total;
      const aspect = isWide
        ? (ratio * scale) / (1 / (row.length * ratio))
        : (1 / (row.length * ratio)) / (ratio * scale);
      worst = Math.max(worst, aspect, 1 / aspect);
    }
    return worst;
  }

  _sumValues(nodes) {
    return nodes.reduce((s, n) => s + n.normalizedValue, 0);
  }

  render(data) {
    if (!data) data = this.data;
    this.ctx.clearRect(0, 0, this.width, this.height);

    for (const node of data) {
      if (node.w < 1 || node.h < 1) continue;

      // Background
      const color = node.change > 0 ? this.options.colors.positive
        : node.change < 0 ? this.options.colors.negative
        : this.options.colors.neutral;

      const alpha = Math.min(0.8, Math.abs(node.change || 0) / 5);
      this.ctx.fillStyle = this._adjustAlpha(color, alpha + 0.2);
      this.ctx.fillRect(node.x, node.y, node.w, node.h);

      // Border
      this.ctx.strokeStyle = this.options.borderColor;
      this.ctx.lineWidth = this.options.borderWidth;
      this.ctx.strokeRect(node.x, node.y, node.w, node.h);

      // Hover effect
      if (node === this.hoveredNode) {
        this.ctx.fillStyle = 'rgba(255,255,255,0.1)';
        this.ctx.fillRect(node.x, node.y, node.w, node.h);
      }

      // Text
      if (node.w > 40 && node.h > 20) {
        this.ctx.fillStyle = this.options.colors.text;
        this.ctx.font = `bold ${this.options.fontSize}px ${this.options.fontFamily}`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';

        const label = node.name || node.code || '';
        const truncated = this._truncateText(label, node.w - 8);
        this.ctx.fillText(truncated, node.x + node.w / 2, node.y + node.h / 2 - 6);

        // Change percentage
        if (node.change !== undefined) {
          this.ctx.font = `${this.options.fontSize - 1}px ${this.options.fontFamily}`;
          this.ctx.fillStyle = node.change >= 0 ? this.options.colors.positive : this.options.colors.negative;
          this.ctx.fillText(
            (node.change >= 0 ? '+' : '') + node.change.toFixed(2) + '%',
            node.x + node.w / 2,
            node.y + node.h / 2 + 8
          );
        }
      }
    }

    // Tooltip
    if (this.hoveredNode) {
      this._drawTooltip(this.hoveredNode);
    }
  }

  _truncateText(text, maxWidth) {
    const metrics = this.ctx.measureText(text);
    if (metrics.width <= maxWidth) return text;

    let truncated = text;
    while (this.ctx.measureText(truncated + '...').width > maxWidth && truncated.length > 0) {
      truncated = truncated.slice(0, -1);
    }
    return truncated + '...';
  }

  _adjustAlpha(color, alpha) {
    if (color.startsWith('rgba')) {
      return color.replace(/[\d.]+\)$/, alpha + ')');
    }
    if (color.startsWith('#')) {
      const r = parseInt(color.slice(1, 3), 16);
      const g = parseInt(color.slice(3, 5), 16);
      const b = parseInt(color.slice(5, 7), 16);
      return `rgba(${r},${g},${b},${alpha})`;
    }
    return color;
  }

  _drawTooltip(node) {
    const padding = 8;
    const lineHeight = 16;
    const lines = [
      node.name || node.code,
      node.sector || '',
      `涨跌: ${(node.change >= 0 ? '+' : '') + (node.change || 0).toFixed(2)}%`,
      `权重: ${(node.normalizedValue * 100).toFixed(1)}%`
    ].filter(Boolean);

    const maxWidth = Math.max(...lines.map(l => this.ctx.measureText(l).width));
    const tooltipWidth = maxWidth + padding * 2;
    const tooltipHeight = lines.length * lineHeight + padding * 2;

    let x = node.x + node.w / 2 - tooltipWidth / 2;
    let y = node.y - tooltipHeight - 4;

    // Keep in bounds
    if (x < 0) x = 0;
    if (x + tooltipWidth > this.width) x = this.width - tooltipWidth;
    if (y < 0) y = node.y + node.h + 4;

    // Background
    this.ctx.fillStyle = 'rgba(0,0,0,0.85)';
    this.ctx.fillRect(x, y, tooltipWidth, tooltipHeight);

    // Text
    this.ctx.fillStyle = '#fff';
    this.ctx.font = `${this.options.fontSize}px ${this.options.fontFamily}`;
    this.ctx.textAlign = 'left';
    this.ctx.textBaseline = 'top';

    lines.forEach((line, i) => {
      this.ctx.fillText(line, x + padding, y + padding + i * lineHeight);
    });
  }

  destroy() {
    this.canvas.removeEventListener('mousemove', this._onMouseMove);
    this.canvas.removeEventListener('mouseleave', this._onMouseLeave);
    this.canvas.removeEventListener('click', this._onClick);
  }
}
