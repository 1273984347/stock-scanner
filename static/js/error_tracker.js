/**
 * 前端错误追踪 —— 捕获 JS 错误和 Promise rejection，上报到后端
 */

export function initErrorTracker() {
    // 捕获未处理的 JS 错误
    window.addEventListener('error', (event) => {
      _reportError({
        type: 'javascript',
        message: event.message,
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
        stack: event.error?.stack,
      });
    });

    // 捕获未处理的 Promise rejection
    window.addEventListener('unhandledrejection', (event) => {
      _reportError({
        type: 'promise_rejection',
        message: String(event.reason?.message || event.reason),
        stack: event.reason?.stack,
      });
    });

    console.log('[ErrorTracker] Initialized');
}

function _reportError(errorData) {
    // 批次 A / P1-1：sendBeacon 带不了自定义 header，后端已要求 X-API-Key。
    // 统一改用 fetch(keepalive) 以保证页面卸载时仍能上报，且与其余 API 请求
    // 走同一套认证边界。只落 pathname，不把完整 URL（可能含查询串凭据）写盘。
    try {
      const payload = {
        ...errorData,
        path: window.location.pathname,
        userAgent: navigator.userAgent,
        timestamp: new Date().toISOString(),
      };

      let apiKey = '';
      try { apiKey = sessionStorage.getItem('api_key') || ''; } catch (e) { /* 隐私模式 */ }

      fetch('/api/errors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify(payload),
        keepalive: true,
      }).catch(() => {}); // 忽略上报失败
    } catch (e) {
      // 不要让错误追踪本身导致问题
    }
}
