export function getThemeColors(scheme) {
  const themes = {
    international: { up: '#26A69A', down: '#EF5350' },
    china: { up: '#EF5350', down: '#26A69A' },
  };
  return themes[scheme] || themes.international;
}

export function formatVolume(vol) {
  if (vol >= 1e8) return (vol / 1e8).toFixed(2) + '亿';
  if (vol >= 1e4) return (vol / 1e4).toFixed(2) + '万';
  return vol.toString();
}

export function formatPrice(price) {
  return price.toFixed(2);
}

export function formatPercent(value) {
  return (value * 100).toFixed(2) + '%';
}
