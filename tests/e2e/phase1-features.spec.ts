import { test, expect } from '@playwright/test';

const BASE_URL = 'http://localhost:8000';

test.describe('Phase 1 Features', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate and set API key
    await page.goto(BASE_URL + '/');
    await page.evaluate(() => {
      sessionStorage.setItem('api_key', 'dev-secret-key');
    });
    // Wait for app to initialize
    await page.waitForTimeout(2000);
  });

  // ─── Strategy Comparison Tab ─────────────────────────────────────────

  test.describe('Strategy Comparison', () => {
    test('strategy tab exists in sidebar', async ({ page }) => {
      const tab = page.locator('.nav-item[data-panel="strategy"]');
      await expect(tab).toBeVisible();
      await expect(tab).toContainText('策略对比');
    });

    test('can switch to strategy tab', async ({ page }) => {
      await page.locator('.nav-item[data-panel="strategy"]').click();
      const panel = page.locator('#panel-strategy');
      await expect(panel).toHaveClass(/active/);
    });

    test('strategy comparison has input fields', async ({ page }) => {
      await page.locator('.nav-item[data-panel="strategy"]').click();
      await expect(page.locator('#scSymbols')).toBeVisible();
      await expect(page.locator('#scCompareBtn')).toBeVisible();
      await expect(page.locator('#scRecommendBtn')).toBeVisible();
      await expect(page.locator('#scHistoryBtn')).toBeVisible();
    });

    test('strategy comparison has sliders', async ({ page }) => {
      await page.locator('.nav-item[data-panel="strategy"]').click();
      await expect(page.locator('#scSL')).toBeVisible();
      await expect(page.locator('#scTP')).toBeVisible();
      await expect(page.locator('#scDays')).toBeVisible();
    });
  });

  // ─── Global Search ──────────────────────────────────────────────────

  test.describe('Global Search', () => {
    test('Ctrl+K opens search overlay', async ({ page }) => {
      await page.keyboard.press('Control+k');
      const overlay = page.locator('#searchOverlay');
      await expect(overlay).toBeVisible();
    });

    test('Escape closes search overlay', async ({ page }) => {
      await page.keyboard.press('Control+k');
      await expect(page.locator('#searchOverlay')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.locator('#searchOverlay')).not.toBeVisible();
    });

    test('search input has correct placeholder', async ({ page }) => {
      await page.keyboard.press('Control+k');
      const input = page.locator('#searchOverlay input');
      await expect(input).toBeVisible();
      await expect(input).toHaveAttribute('placeholder', /搜索/);
    });

    test('clicking overlay background closes search', async ({ page }) => {
      await page.keyboard.press('Control+k');
      const overlay = page.locator('#searchOverlay');
      await overlay.click({ position: { x: 10, y: 10 } });
      await expect(overlay).not.toBeVisible();
    });
  });

  // ─── Navigation ─────────────────────────────────────────────────────

  test.describe('Navigation', () => {
    test('sidebar has 12 tabs', async ({ page }) => {
      const tabs = page.locator('.nav-item');
      await expect(tabs).toHaveCount(12);
    });

    test('breadcrumb is visible in toolbar', async ({ page }) => {
      const breadcrumb = page.locator('.toolbar-breadcrumb');
      await expect(breadcrumb).toBeVisible();
    });

    test('j/k keyboard shortcuts switch tabs', async ({ page }) => {
      // Get initial active tab
      const initialTab = await page.locator('.nav-item.active').getAttribute('data-panel');
      // Press j to go to next tab
      await page.keyboard.press('j');
      const nextTab = await page.locator('.nav-item.active').getAttribute('data-panel');
      expect(nextTab).not.toBe(initialTab);
    });
  });

  // ─── Pagination ─────────────────────────────────────────────────────

  test.describe('Pagination', () => {
    test('positions panel has pagination container', async ({ page }) => {
      await page.locator('.nav-item[data-panel="positions"]').click();
      const pagination = page.locator('#posPagination');
      await expect(pagination).toBeAttached();
    });

    test('trades panel has pagination container', async ({ page }) => {
      await page.locator('.nav-item[data-panel="trades"]').click();
      const pagination = page.locator('#tradePagination');
      await expect(pagination).toBeAttached();
    });

    test('signals panel has pagination container', async ({ page }) => {
      await page.locator('.nav-item[data-panel="signals"]').click();
      const pagination = page.locator('#signalPagination');
      await expect(pagination).toBeAttached();
    });
  });

  // ─── Charts ─────────────────────────────────────────────────────────

  test.describe('Charts', () => {
    test('portfolio has equity curve canvas', async ({ page }) => {
      const canvas = page.locator('#equityCurveChart');
      await expect(canvas).toBeAttached();
    });

    test('eval panel has IC history chart canvas', async ({ page }) => {
      await page.locator('.nav-item[data-panel="exposures"]').click();
      const canvas = page.locator('#icHistoryChart');
      await expect(canvas).toBeAttached();
    });

    test('signals panel has accuracy trend chart', async ({ page }) => {
      await page.locator('.nav-item[data-panel="signals"]').click();
      const canvas = page.locator('#accuracyTrendChart');
      await expect(canvas).toBeAttached();
    });

    test('risk panel has correlation heatmap', async ({ page }) => {
      await page.locator('.nav-item[data-panel="risk"]').click();
      const canvas = page.locator('#correlationHeatmap');
      await expect(canvas).toBeAttached();
    });
  });

  // ─── K-line Chart ───────────────────────────────────────────────────

  test.describe('K-line Chart', () => {
    test('kline range container exists', async ({ page }) => {
      const container = page.locator('#klineRangeContainer');
      await expect(container).toBeAttached();
    });
  });

  // ─── Console Errors ─────────────────────────────────────────────────

  test('no critical console errors on load', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    await page.goto(BASE_URL + '/');
    await page.waitForTimeout(2000);
    const criticalErrors = errors.filter(
      (e) => !e.includes('WebSocket') && !e.includes('net::ERR') && !e.includes('Failed to fetch')
        && !e.includes('Content Security Policy') && !e.includes('fonts.googleapis.com')
        && !e.includes('422') && !e.includes('401')
    );
    expect(criticalErrors).toHaveLength(0);
  });
});
