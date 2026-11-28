/**
 * Theme utility — reads CSS custom properties for chart theming
 */
export function getThemeColor(varName) {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

/**
 * 图表语义色 —— 随主题 token 自动切换（避免硬编码 GitHub 色残留）
 */
export function chartPalette() {
  return {
    accent: getThemeColor('--accent'),
    up: getThemeColor('--up'),
    down: getThemeColor('--down'),
    warn: getThemeColor('--warn'),
    text: getThemeColor('--text-secondary'),
    dim: getThemeColor('--text-dim'),
    border: getThemeColor('--border'),
    grid: getThemeColor('--border'),
  };
}

/** 多序列取色：主序列用强调色，其余在语义色间轮转 */
export function seriesColors(n) {
  const p = chartPalette();
  const ring = [p.accent, p.up, p.down, p.warn, p.dim];
  const out = [];
  for (let i = 0; i < n; i++) out.push(ring[i % ring.length]);
  return out;
}
