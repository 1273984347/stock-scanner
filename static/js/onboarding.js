/**
 * 首次使用引导 —— 分步教程
 *
 * 在用户第一次访问时显示，介绍各功能区域。
 * 使用 localStorage 记录是否已完成引导。
 */

const TOUR_STEPS = [
  {
    target: '.metrics',
    title: '核心指标',
    content: '这里显示市场情绪、宏观门控、建议仓位等核心指标。每次刷新数据时自动更新。',
    position: 'bottom',
  },
  {
    target: '.nav-tabs',
    title: '功能导航',
    content: '使用 j/k 键或点击切换不同功能面板。共 13 个功能区域。',
    position: 'bottom',
  },
  {
    target: '#panel-portfolio',
    title: '组合优化',
    content: '默认页面。显示优化后的持仓配置、行业暴露和收益预期。',
    position: 'top',
  },
  {
    target: '#panel-scanner',
    title: '选股结果',
    content: '点击进入查看各策略的选股结果。点击股票可查看 K 线图和技术指标。',
    position: 'top',
  },
  {
    target: '#panel-signals',
    title: '信号追踪',
    content: '查看历史信号准确率和最近信号记录。系统自动追踪每个信号的实际表现。',
    position: 'top',
  },
  {
    target: '.theme-toggle',
    title: '主题切换',
    content: '点击切换暗色/亮色主题。支持跟随系统偏好。',
    position: 'bottom',
  },
  {
    target: '#refreshBtn',
    title: '刷新数据',
    content: '点击或按 r 键刷新所有数据。数据每 10 秒通过 WebSocket 自动更新。',
    position: 'bottom',
  },
];

export class Onboarding {
  constructor() {
    this._currentStep = 0;
    this._overlay = null;
    this._tooltip = null;
  }

  start() {
    if (localStorage.getItem('onboarding_complete') === 'true') return;
    if (TOUR_STEPS.length === 0) return;

    this._currentStep = 0;
    this._createOverlay();
    this._showStep(0);
  }

  _createOverlay() {
    this._overlay = document.createElement('div');
    this._overlay.id = 'onboardingOverlay';
    this._overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:999;pointer-events:none';
    document.body.appendChild(this._overlay);

    this._tooltip = document.createElement('div');
    this._tooltip.id = 'onboardingTooltip';
    this._tooltip.style.cssText = 'position:fixed;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;max-width:300px;z-index:1000;box-shadow:0 4px 20px rgba(0,0,0,0.3)';
    document.body.appendChild(this._tooltip);
  }

  _showStep(index) {
    if (index >= TOUR_STEPS.length) {
      this._complete();
      return;
    }

    const step = TOUR_STEPS[index];
    const target = document.querySelector(step.target);
    if (!target) {
      this._showStep(index + 1);
      return;
    }

    // Highlight target via clip mask
    const rect = target.getBoundingClientRect();
    this._overlay.style.background = 'rgba(0,0,0,0.5)';
    this._overlay.style.clipPath = `polygon(
      0% 0%, 100% 0%, 100% ${rect.top}px,
      0% ${rect.top}px, 0% ${rect.bottom}px,
      100% ${rect.bottom}px, 100% 100%, 0% 100%
    )`;

    // Position tooltip
    const tooltipY = step.position === 'bottom' ? rect.bottom + 10 : rect.top - 120;
    this._tooltip.style.top = Math.max(10, tooltipY) + 'px';
    this._tooltip.style.left = Math.max(10, rect.left) + 'px';

    // Build tooltip content with safe DOM methods
    this._tooltip.textContent = '';
    const titleEl = document.createElement('div');
    titleEl.style.cssText = 'font-weight:bold;margin-bottom:8px';
    titleEl.textContent = step.title;
    this._tooltip.appendChild(titleEl);

    const contentEl = document.createElement('div');
    contentEl.style.cssText = 'font-size:13px;color:var(--text-sub);margin-bottom:12px';
    contentEl.textContent = step.content;
    this._tooltip.appendChild(contentEl);

    const footer = document.createElement('div');
    footer.style.cssText = 'display:flex;justify-content:space-between;align-items:center';

    const counter = document.createElement('span');
    counter.style.cssText = 'font-size:11px;color:var(--text-sub)';
    counter.textContent = `${index + 1}/${TOUR_STEPS.length}`;
    footer.appendChild(counter);

    const btnGroup = document.createElement('div');

    const skipBtn = document.createElement('button');
    skipBtn.id = 'tourSkip';
    skipBtn.style.cssText = 'padding:4px 8px;border:1px solid var(--border);border-radius:4px;background:var(--card);color:var(--text);cursor:pointer;margin-right:8px';
    skipBtn.textContent = '跳过';
    skipBtn.addEventListener('click', () => this._complete());
    btnGroup.appendChild(skipBtn);

    const nextBtn = document.createElement('button');
    nextBtn.id = 'tourNext';
    nextBtn.style.cssText = 'padding:4px 12px;border:none;border-radius:4px;background:var(--accent);color:white;cursor:pointer';
    nextBtn.textContent = index < TOUR_STEPS.length - 1 ? '下一步' : '完成';
    nextBtn.addEventListener('click', () => this._showStep(index + 1));
    btnGroup.appendChild(nextBtn);

    footer.appendChild(btnGroup);
    this._tooltip.appendChild(footer);
  }

  _complete() {
    localStorage.setItem('onboarding_complete', 'true');
    this._overlay?.remove();
    this._tooltip?.remove();
  }
}
