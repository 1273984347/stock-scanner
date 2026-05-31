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
    // 不要阻塞主线程，用 sendBeacon 或 fire-and-forget fetch
    try {
      const payload = {
        ...errorData,
        url: window.location.href,
        userAgent: navigator.userAgent,
        timestamp: new Date().toISOString(),
      };

      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/errors', JSON.stringify(payload));
      } else {
        fetch('/api/errors', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          keepalive: true,
        }).catch(() => {}); // 忽略上报失败
      }
    } catch (e) {
      // 不要让错误追踪本身导致问题
    }
}
