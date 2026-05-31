import { apiClient } from '../api.js';

export class SettingsRenderer {
  async mount() {
    await this._loadSettings();
    document.getElementById('saveSettingsBtn')?.addEventListener('click', () => this._saveSettings());
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
    } catch (e) {
      console.error('[Settings] load failed:', e);
    }
  }

  async _saveSettings() {
    const get = (id) => document.getElementById(id)?.value;
    const checked = (id) => document.getElementById(id)?.checked;

    const settings = {
      default_preset: get('settingPreset') || 'balanced',
      default_days: parseInt(get('settingDays')) || 250,
      default_symbols: get('settingSymbols') || '',
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
