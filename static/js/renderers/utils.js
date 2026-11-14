// utils.js — 工具函数

// 批次 A / P0-2：全站唯一的 HTML 转义实现。
// 此前 eval / audit / risk / signals / exposures 各自复制了一份，
// 其中 exposures.js 定义了 esc() 却一次都没调用 —— 复制即漂移。
export function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function fmtMoney(v) { if (v == null) return '--'; const n = Number(v); if (isNaN(n)) return String(v); if (Math.abs(n) >= 1e8) return (n/1e8).toFixed(2)+'亿'; if (Math.abs(n) >= 1e4) return (n/1e4).toFixed(0)+'万'; return n.toFixed(0); }
export function fmtPct(v) { if (v == null) return '--'; return (Number(v)*100).toFixed(1)+'%'; }
export function fmtPctChg(v) { if (v == null) return '--'; const n = Number(v); return (n > 0 ? '+' : '') + n.toFixed(2) + '%'; }
