/**
 * animation.js - 动画工具函数
 * 提供数字滚动、排名变化、入场动画等效果
 */

/**
 * 数字滚动动画
 * @param {HTMLElement} element - 目标元素
 * @param {number} startValue - 起始值
 * @param {number} endValue - 结束值
 * @param {number} duration - 动画时长 (ms)
 * @param {Object} options - 选项
 */
export function animateNumber(element, startValue, endValue, duration = 500, options = {}) {
  const {
    decimals = 2,
    prefix = '',
    suffix = '',
    onComplete = null
  } = options;

  const startTime = performance.now();
  const diff = endValue - startValue;

  function update(currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);

    // 使用 easeOutCubic 缓动函数
    const eased = 1 - Math.pow(1 - progress, 3);
    const currentValue = startValue + diff * eased;

    element.textContent = prefix + currentValue.toFixed(decimals) + suffix;

    if (progress < 1) {
      requestAnimationFrame(update);
    } else {
      element.textContent = prefix + endValue.toFixed(decimals) + suffix;
      if (onComplete) onComplete();
    }
  }

  requestAnimationFrame(update);
}

/**
 * 排名变化动画
 * @param {HTMLElement} element - 目标元素
 * @param {number} fromY - 起始 Y 位置
 * @param {number} toY - 目标 Y 位置
 * @param {number} duration - 动画时长 (ms)
 */
export function animatePosition(element, fromY, toY, duration = 300) {
  element.style.transform = `translateY(${fromY}px)`;
  element.style.transition = 'none';

  requestAnimationFrame(() => {
    element.style.transition = `transform ${duration}ms ease-out`;
    element.style.transform = `translateY(${toY}px)`;

    setTimeout(() => {
      element.style.transform = '';
      element.style.transition = '';
    }, duration);
  });
}

/**
 * 入场动画
 * @param {HTMLElement} element - 目标元素
 * @param {string} type - 动画类型 ('fade-in', 'slide-up', 'scale-in')
 * @param {number} duration - 动画时长 (ms)
 * @param {number} delay - 延迟 (ms)
 */
export function animateEntrance(element, type = 'fade-in', duration = 300, delay = 0) {
  element.style.opacity = '0';
  element.style.transition = 'none';

  if (type === 'slide-up') {
    element.style.transform = 'translateY(20px)';
  } else if (type === 'scale-in') {
    element.style.transform = 'scale(0.9)';
  }

  setTimeout(() => {
    element.style.transition = `opacity ${duration}ms ease-out, transform ${duration}ms ease-out`;
    element.style.opacity = '1';
    element.style.transform = '';

    setTimeout(() => {
      element.style.transition = '';
    }, duration);
  }, delay);
}

/**
 * 颜色渐变动画
 * @param {HTMLElement} element - 目标元素
 * @param {string} fromColor - 起始颜色
 * @param {string} toColor - 目标颜色
 * @param {number} duration - 动画时长 (ms)
 */
export function animateColor(element, fromColor, toColor, duration = 300) {
  element.style.backgroundColor = fromColor;
  element.style.transition = 'none';

  requestAnimationFrame(() => {
    element.style.transition = `background-color ${duration}ms ease-out`;
    element.style.backgroundColor = toColor;

    setTimeout(() => {
      element.style.transition = '';
    }, duration);
  });
}

/**
 * 批量入场动画
 * @param {HTMLElement[]} elements - 元素数组
 * @param {string} type - 动画类型
 * @param {number} stagger - 错开时间 (ms)
 * @param {number} duration - 动画时长 (ms)
 */
export function animateStagger(elements, type = 'fade-in', stagger = 50, duration = 300) {
  elements.forEach((el, index) => {
    animateEntrance(el, type, duration, index * stagger);
  });
}

/**
 * Chart.js 动画配置
 */
export const chartAnimationConfig = {
  animation: {
    duration: 500,
    easing: 'easeOutCubic'
  },
  transitions: {
    active: {
      animation: {
        duration: 200
      }
    }
  }
};

/**
 * 检查用户是否偏好减少动画
 * @returns {boolean}
 */
export function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/**
 * 根据用户偏好选择动画时长
 * @param {number} duration - 期望时长
 * @returns {number} 实际时长
 */
export function getAnimationDuration(duration) {
  return prefersReducedMotion() ? 0 : duration;
}
