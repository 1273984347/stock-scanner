/**
 * Browser Push Notifications
 * Manages permission requests and sends desktop notifications.
 */

export function initNotifications() {
    if (!('Notification' in window)) {
      console.log('[Notifications] Browser does not support notifications');
      return;
    }

    if (Notification.permission === 'default') {
      const btn = document.getElementById('enableNotifications');
      if (btn) {
        btn.style.display = 'inline-block';
        btn.addEventListener('click', async () => {
          const permission = await Notification.requestPermission();
          if (permission === 'granted') {
            btn.style.display = 'none';
            localStorage.setItem('notifications_enabled', 'true');
            new Notification('通知已开启', { body: '您将收到信号和交易通知' });
          }
        });
      }
    } else if (Notification.permission === 'granted') {
      localStorage.setItem('notifications_enabled', 'true');
    }
}

export function sendBrowserNotification(title, body, tag) {
    if (Notification.permission !== 'granted') return;
    if (localStorage.getItem('notifications_enabled') !== 'true') return;

    try {
      new Notification(title, {
        body,
        tag: tag || 'stock-scanner',
        icon: '/static/favicon.ico',
      });
    } catch (e) {
      console.debug('[Notification] failed:', e);
    }
}
