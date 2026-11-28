#!/usr/bin/env node
/**
 * build_ui_variants.mjs —— 生成四个前端风格变体（自包含单文件，双击即开）
 *
 * A 信号台 Signal Deck —— 决策流程轴 + 阈值仪表带 + 证据栏（控制台）
 * B 盘后日报 Daily Brief —— 章节化叙事 + 表格（报纸）
 * C 扫描矩阵 Matrix —— 一屏高密度大表 + 底部抽屉（终端）
 * D 驾驶舱 Cockpit —— 大读数块 + 并排视图（概览看板）
 *
 * 共同约束：纯黑白灰；涨跌红绿两套可切；主题 = 深色/浅色/跟随系统。
 * 用法：node scripts/build_ui_variants.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.join(ROOT, 'docs', 'preview');
fs.mkdirSync(OUT, { recursive: true });

const TOKENS = fs.readFileSync(path.join(ROOT, 'cache', 'shared-tokens.css'), 'utf8');

const HEAD = (title, extraCss) => [
  '<!DOCTYPE html>',
  '<html lang="zh-CN" data-theme="dark">',
  '<head>',
  '<meta charset="UTF-8">',
  '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
  '<!-- 共享 token：纯黑白灰 + 涨跌双色 -->',
  '<style>',
  TOKENS,
  extraCss,
  '</style>',
  '</head>',
  '<body>',
  '<header class="top">',
  '  <div class="mark"><b>QUANT A-SHARE</b><span>' + title + '</span></div>',
  '  <div class="top-spacer"></div>',
  '  <button class="btn" id="themeBtn">主题</button>',
  '  <button class="btn" id="csBtn">红涨绿跌</button>',
  '</header>',
].join('\n');

const TAIL = [
  '<script>',
].join('\n');

const CLOSE = [
  '</script>',
  '</body>',
  '</html>',
  ''
].join('\n');

function write(name, title, extraCss, body, js) {
  const html = HEAD(title, extraCss) + body + TAIL + js + CLOSE;
  const f = path.join(OUT, name + '.html');
  fs.writeFileSync(f, html, 'utf8');
  console.log('[variant] ' + path.relative(ROOT, f) + '  (' + (html.length / 1024).toFixed(1) + ' KB)');
}

export { write };
console.log('[variants] 模块已加载：由调用方提供各变体定义');
