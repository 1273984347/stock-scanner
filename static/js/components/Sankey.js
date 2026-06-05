/**
 * Sankey.js - 桑基图组件
 * 使用 Canvas 绘制资金流向图
 */

export class Sankey {
  constructor(canvas, options = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.data = { nodes: [], links: [] };
    this.hoveredNode = null;
    this.hoveredLink = null;
    this.options = {
      nodeWidth: 20,
      nodePadding: 10,
      fontSize: 11,
      fontFamily: 'Inter, sans-serif',
      colors: {
        inflow: '#00c853',
        outflow: '#ff1744',
        neutral: '#787B86',
        node: '#2962ff',
        nodeHover: '#1e53e0',
        link: 'rgba(41, 98, 255, 0.3)',
        linkHover: 'rgba(41, 98, 255, 0.6)',
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
    window.addEventListener('resize', () => {
      this._setupCanvas();
      this.render();
    });
  }

  _onMouseMove(e) {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    const node = this._findNode(x, y);
    const link = node ? null : this._findLink(x, y);

    if (node !== this.hoveredNode || link !== this.hoveredLink) {
      this.hoveredNode = node;
      this.hoveredLink = link;
      this.canvas.style.cursor = (node || link) ? 'pointer' : 'default';
      this.render();
    }
  }

  _onMouseLeave() {
    if (this.hoveredNode || this.hoveredLink) {
      this.hoveredNode = null;
      this.hoveredLink = null;
      this.canvas.style.cursor = 'default';
      this.render();
    }
  }

  _findNode(x, y) {
    for (const node of this.data.nodes) {
      if (x >= node.x && x <= node.x + node.width &&
          y >= node.y && y <= node.y + node.height) {
        return node;
      }
    }
    return null;
  }

  _findLink(x, y) {
    for (const link of this.data.links) {
      if (this._isPointOnLink(x, y, link)) {
        return link;
      }
    }
    return null;
  }

  _isPointOnLink(x, y, link) {
    // Simple bounding box check for curved path
    const minX = Math.min(link.sourceX + link.sourceWidth, link.targetX);
    const maxX = Math.max(link.sourceX + link.sourceWidth, link.targetX);
    const minY = Math.min(link.sourceY, link.targetY);
    const maxY = Math.max(link.sourceY + link.sourceHeight, link.targetY + link.targetHeight);

    if (x < minX || x > maxX || y < minY || y > maxY) return false;

    // Check if point is near the curve
    const t = (x - (link.sourceX + link.sourceWidth)) / (link.targetX - link.sourceX - link.sourceWidth);
    if (t < 0 || t > 1) return false;

    const startY = link.sourceY + link.sourceHeight / 2;
    const endY = link.targetY + link.targetHeight / 2;
    const cpX = (link.sourceX + link.sourceWidth + link.targetX) / 2;
    const cpY1 = startY;
    const cpY2 = endY;

    // Bezier curve Y at t
    const yOnCurve = Math.pow(1 - t, 3) * startY +
                     3 * Math.pow(1 - t, 2) * t * cpY1 +
                     3 * (1 - t) * t * t * cpY2 +
                     t * t * t * endY;

    return Math.abs(y - yOnCurve) < link.value * 2 + 5;
  }

  setData(data) {
    this.data = this._processData(data);
    this.render();
  }

  _processData(data) {
    if (!data || !data.nodes || !data.links) {
      return { nodes: [], links: [] };
    }

    const { nodes, links } = data;
    const nodeCount = nodes.length;
    if (nodeCount === 0) return { nodes: [], links: [] };

    // Calculate node positions
    const nodeWidth = this.options.nodeWidth;
    const padding = this.options.nodePadding;
    const availableHeight = this.height - padding * (nodeCount + 1);
    const nodeHeight = availableHeight / nodeCount;

    const processedNodes = nodes.map((node, i) => ({
      ...node,
      x: i < nodeCount / 2 ? 0 : this.width - nodeWidth,
      y: padding + i * (nodeHeight + padding),
      width: nodeWidth,
      height: nodeHeight,
      index: i
    }));

    // Calculate link values and positions
    const totalValue = links.reduce((sum, l) => sum + l.value, 0);
    const processedLinks = links.map(link => {
      const source = processedNodes[link.source];
      const target = processedNodes[link.target];
      const valueRatio = link.value / totalValue;

      return {
        ...link,
        sourceX: source.x,
        sourceY: source.y,
        sourceWidth: source.width,
        sourceHeight: source.height * valueRatio,
        targetX: target.x,
        targetY: target.y,
        targetWidth: target.width,
        targetHeight: target.height * valueRatio,
        valueRatio
      };
    });

    return { nodes: processedNodes, links: processedLinks };
  }

  render() {
    const { nodes, links } = this.data;
    this.ctx.clearRect(0, 0, this.width, this.height);

    // Draw links
    for (const link of links) {
      this._drawLink(link);
    }

    // Draw nodes
    for (const node of nodes) {
      this._drawNode(node);
    }

    // Draw tooltips
    if (this.hoveredNode) {
      this._drawNodeTooltip(this.hoveredNode);
    } else if (this.hoveredLink) {
      this._drawLinkTooltip(this.hoveredLink);
    }
  }

  _drawNode(node) {
    const { ctx, options } = this;
    const isHovered = node === this.hoveredNode;

    // Node rectangle
    ctx.fillStyle = isHovered ? options.colors.nodeHover : options.colors.node;
    ctx.fillRect(node.x, node.y, node.width, node.height);

    // Border
    ctx.strokeStyle = 'rgba(255,255,255,0.3)';
    ctx.lineWidth = 1;
    ctx.strokeRect(node.x, node.y, node.width, node.height);

    // Label
    if (node.height > 15) {
      ctx.fillStyle = options.colors.text;
      ctx.font = `bold ${options.fontSize}px ${options.fontFamily}`;
      ctx.textAlign = node.x === 0 ? 'right' : 'left';
      ctx.textBaseline = 'middle';

      const labelX = node.x === 0 ? node.x - 6 : node.x + node.width + 6;
      const label = node.name || '';
      const truncated = this._truncateText(label, 80);
      ctx.fillText(truncated, labelX, node.y + node.height / 2);
    }
  }

  _drawLink(link) {
    const { ctx, options } = this;
    const isHovered = link === this.hoveredLink;

    ctx.fillStyle = isHovered ? options.colors.linkHover : options.colors.link;

    const startX = link.sourceX + link.sourceWidth;
    const startY = link.sourceY + link.sourceHeight / 2;
    const endX = link.targetX;
    const endY = link.targetY + link.targetHeight / 2;
    const cpX = (startX + endX) / 2;

    ctx.beginPath();
    ctx.moveTo(startX, startY - link.sourceHeight / 2);
    ctx.bezierCurveTo(cpX, startY, cpX, endY, endX, endY - link.targetHeight / 2);
    ctx.lineTo(endX, endY + link.targetHeight / 2);
    ctx.bezierCurveTo(cpX, endY, cpX, startY, startX, startY + link.sourceHeight / 2);
    ctx.closePath();
    ctx.fill();
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

  _drawNodeTooltip(node) {
    const { ctx, options } = this;
    const padding = 8;
    const lineHeight = 16;
    const lines = [
      node.name,
      `权重: ${(node.valueRatio * 100).toFixed(1)}%`
    ];

    const maxWidth = Math.max(...lines.map(l => ctx.measureText(l).width));
    const tooltipWidth = maxWidth + padding * 2;
    const tooltipHeight = lines.length * lineHeight + padding * 2;

    let x = node.x + node.width / 2 - tooltipWidth / 2;
    let y = node.y - tooltipHeight - 4;

    if (x < 0) x = 0;
    if (x + tooltipWidth > this.width) x = this.width - tooltipWidth;
    if (y < 0) y = node.y + node.height + 4;

    ctx.fillStyle = 'rgba(0,0,0,0.85)';
    ctx.fillRect(x, y, tooltipWidth, tooltipHeight);

    ctx.fillStyle = '#fff';
    ctx.font = `${options.fontSize}px ${options.fontFamily}`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';

    lines.forEach((line, i) => {
      ctx.fillText(line, x + padding, y + padding + i * lineHeight);
    });
  }

  _drawLinkTooltip(link) {
    const { ctx, options } = this;
    const padding = 8;
    const lineHeight = 16;
    const sourceName = this.data.nodes[link.source]?.name || '';
    const targetName = this.data.nodes[link.target]?.name || '';
    const lines = [
      `${sourceName} → ${targetName}`,
      `流量: ${(link.valueRatio * 100).toFixed(2)}%`
    ];

    const maxWidth = Math.max(...lines.map(l => ctx.measureText(l).width));
    const tooltipWidth = maxWidth + padding * 2;
    const tooltipHeight = lines.length * lineHeight + padding * 2;

    const centerX = (link.sourceX + link.sourceWidth + link.targetX) / 2;
    const centerY = (link.sourceY + link.targetY) / 2;

    let x = centerX - tooltipWidth / 2;
    let y = centerY - tooltipHeight - 4;

    if (x < 0) x = 0;
    if (x + tooltipWidth > this.width) x = this.width - tooltipWidth;
    if (y < 0) y = centerY + 4;

    ctx.fillStyle = 'rgba(0,0,0,0.85)';
    ctx.fillRect(x, y, tooltipWidth, tooltipHeight);

    ctx.fillStyle = '#fff';
    ctx.font = `${options.fontSize}px ${options.fontFamily}`;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';

    lines.forEach((line, i) => {
      ctx.fillText(line, x + padding, y + padding + i * lineHeight);
    });
  }

  destroy() {
    this.canvas.removeEventListener('mousemove', this._onMouseMove);
    this.canvas.removeEventListener('mouseleave', this._onMouseLeave);
  }
}
