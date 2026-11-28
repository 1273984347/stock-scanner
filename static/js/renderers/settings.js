import { apiClient } from '/js/api.js';
import { i18n } from '/js/i18n.js';

export class SettingsRenderer {
  async mount() {
    await this._loadSettings();
    document.getElementById('saveSettingsBtn')?.addEventListener('click', () => this._saveSettings());
    // 加载字体大小
    this._loadFontSize();
    // 加载语言设置
    this._loadLocale();
    // 加载 RTL 设置
    this._loadDirection();
  }

  _loadFontSize() {
    const saved = localStorage.getItem('fontSize') || '14';
    this._applyFontSize(saved);
    const select = document.getElementById('settingFontSize');
    if (select) select.value = saved;
  }

  _applyFontSize(size) {
    document.documentElement.style.setProperty('--font-size-base', size + 'px');
    localStorage.setItem('fontSize', size);
  }

  _loadLocale() {
    const saved = localStorage.getItem('stock_scanner_locale') || 'zh';
    const select = document.getElementById('settingLocale');
    if (select) select.value = saved;
  }

  _loadDirection() {
    const saved = localStorage.getItem('direction') || 'ltr';
    document.documentElement.dir = saved;
    const select = document.getElementById('settingDirection');
    if (select) select.value = saved;
  }

  async _loadSettings() {
    try {
      const settings = await apiClient.get('/api/settings');
      if (!settings) return;

      const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) {
          if (el.type === 'checkbox') el.checked = !!val;
          else el.value = val || '';
        }
      };

      set('settingPreset', settings.default_preset);
      set('settingDays', settings.default_days);
      set('settingSymbols', settings.default_symbols);
      set('settingNotifySignals', settings.notifications?.signals);
      set('settingNotifyTrades', settings.notifications?.trades);
      set('settingNotifyAlerts', settings.notifications?.alerts);
      set('settingNotifyDaily', settings.notifications?.daily_report);
      // 加载字体大小设置
      if (settings.font_size) {
        set('settingFontSize', settings.font_size);
        this._applyFontSize(settings.font_size);
      }
    } catch (e) {
      console.error('[Settings] load failed:', e);
    }
  }

  async _saveSettings() {
    const get = (id) => document.getElementById(id)?.value;
    const checked = (id) => document.getElementById(id)?.checked;

    const fontSize = get('settingFontSize') || '14';
    this._applyFontSize(fontSize);

    // 保存语言设置
    const locale = get('settingLocale') || 'zh';
    i18n.locale = locale;

    // 保存 RTL 设置
    const direction = get('settingDirection') || 'ltr';
    document.documentElement.dir = direction;
    localStorage.setItem('direction', direction);

    const settings = {
      default_preset: get('settingPreset') || 'balanced',
      default_days: parseInt(get('settingDays')) || 250,
      default_symbols: get('settingSymbols') || '',
      font_size: fontSize,
      locale: locale,
      direction: direction,
      notifications: {
        signals: checked('settingNotifySignals'),
        trades: checked('settingNotifyTrades'),
        alerts: checked('settingNotifyAlerts'),
        daily_report: checked('settingNotifyDaily'),
      },
    };

    try {
      await apiClient.post('/api/settings', settings);
      const status = document.getElementById('settingsStatus');
      if (status) {
        status.textContent = '✓ 已保存';
        status.style.color = 'var(--up)';
        setTimeout(() => { status.textContent = ''; }, 3000);
      }
    } catch (e) {
      console.error('[Settings] save failed:', e);
    }
  }
}
