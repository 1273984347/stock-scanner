/**
 * i18n.js - 轻量级多语言框架
 * 支持语言切换、日期/数字格式化、动态 re-render
 */

const DEFAULT_LOCALE = 'zh';
const LOCALE_KEY = 'stock_scanner_locale';

class I18nManager {
  constructor() {
    this._locale = localStorage.getItem(LOCALE_KEY) || this._detectLocale();
    this._translations = {};
    this._listeners = [];
    this._formatters = {};
  }

  _detectLocale() {
    const lang = navigator.language || navigator.userLanguage || DEFAULT_LOCALE;
    return lang.startsWith('zh') ? 'zh' : 'en';
  }

  async loadTranslations() {
    try {
      const [zh, en] = await Promise.all([
        fetch('/locales/zh.json').then(r => r.json()),
        fetch('/locales/en.json').then(r => r.json())
      ]);
      this._translations = { zh, en };
    } catch (e) {
      console.error('[i18n] Failed to load translations:', e);
      this._translations = { zh: {}, en: {} };
    }
  }

  get locale() {
    return this._locale;
  }

  set locale(value) {
    if (this._locale !== value && this._translations[value]) {
      this._locale = value;
      localStorage.setItem(LOCALE_KEY, value);
      this._notifyListeners();
    }
  }

  t(key, params = {}) {
    const dict = this._translations[this._locale] || {};
    let text = dict[key] || key;

    // 参数替换：{name} → value
    Object.entries(params).forEach(([k, v]) => {
      text = text.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
    });

    return text;
  }

  formatDate(date, options = {}) {
    const defaults = {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      ...options
    };
    return new Intl.DateTimeFormat(this._locale, defaults).format(new Date(date));
  }

  formatNumber(number, options = {}) {
    const defaults = {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2,
      ...options
    };
    return new Intl.NumberFormat(this._locale, defaults).format(number);
  }

  formatPercent(number, options = {}) {
    const defaults = {
      style: 'percent',
      minimumFractionDigits: 1,
      maximumFractionDigits: 2,
      ...options
    };
    return new Intl.NumberFormat(this._locale, defaults).format(number / 100);
  }

  formatCurrency(number, currency = 'CNY', options = {}) {
    const defaults = {
      style: 'currency',
      currency: currency,
      minimumFractionDigits: 2,
      ...options
    };
    return new Intl.NumberFormat(this._locale, defaults).format(number);
  }

  onChange(callback) {
    this._listeners.push(callback);
    return () => {
      this._listeners = this._listeners.filter(l => l !== callback);
    };
  }

  _notifyListeners() {
    this._listeners.forEach(cb => {
      try { cb(this._locale); } catch (e) { console.error('[i18n] Listener error:', e); }
    });
  }
}

// 单例导出
export const i18n = new I18nManager();
