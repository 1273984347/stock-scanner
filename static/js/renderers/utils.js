// utils.js — 工具函数
export function fmtMoney(v) { if (v == null) return '--'; const n = Number(v); if (isNaN(n)) return String(v); if (Math.abs(n) >= 1e8) return (n/1e8).toFixed(2)+'亿'; if (Math.abs(n) >= 1e4) return (n/1e4).toFixed(0)+'万'; return n.toFixed(0); }
export function fmtPct(v) { if (v == null) return '--'; return (Number(v)*100).toFixed(1)+'%'; }
export function fmtPctChg(v) { if (v == null) return '--'; const n = Number(v); return (n > 0 ? '+' : '') + n.toFixed(2) + '%'; }
