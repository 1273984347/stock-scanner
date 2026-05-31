import { test, expect } from '@playwright/test';

test.describe('Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('loads home page with correct title', async ({ page }) => {
    await expect(page).toHaveTitle(/量化/);
  });

  test('displays 6 metric cards', async ({ page }) => {
    const metrics = page.locator('.metric-card');
    await expect(metrics).toHaveCount(6);
  });

  test('metric cards show expected labels', async ({ page }) => {
    await expect(page.locator('.metric-card').nth(0)).toContainText('市场情绪');
    await expect(page.locator('.metric-card').nth(1)).toContainText('宏观门控');
    await expect(page.locator('.metric-card').nth(2)).toContainText('建议仓位');
    await expect(page.locator('.metric-card').nth(3)).toContainText('组合夏普');
    await expect(page.locator('.metric-card').nth(4)).toContainText('持仓数');
    await expect(page.locator('.metric-card').nth(5)).toContainText('候选股');
  });

  test('has navigation tabs', async ({ page }) => {
    const tabs = page.locator('.nav-tab');
    await expect(tabs.first()).toBeVisible();
    // Should have 12 tabs
    await expect(tabs).toHaveCount(12);
  });

  test('first tab (portfolio) is active by default', async ({ page }) => {
    const firstTab = page.locator('.nav-tab').first();
    await expect(firstTab).toHaveClass(/active/);
    const firstPanel = page.locator('#panel-portfolio');
    await expect(firstPanel).toHaveClass(/active/);
  });

  test('can switch to scanner tab', async ({ page }) => {
    await page.locator('.nav-tab[data-panel="scanner"]').click();
    const panel = page.locator('#panel-scanner');
    await expect(panel).toHaveClass(/active/);
    await expect(page.locator('.nav-tab[data-panel="scanner"]')).toHaveClass(/active/);
  });

  test('can switch to risk tab', async ({ page }) => {
    await page.locator('.nav-tab[data-panel="risk"]').click();
    const panel = page.locator('#panel-risk');
    await expect(panel).toHaveClass(/active/);
  });

  test('can switch to settings tab', async ({ page }) => {
    await page.locator('.nav-tab[data-panel="settings"]').click();
    const panel = page.locator('#panel-settings');
    await expect(panel).toHaveClass(/active/);
  });

  test('refresh button is visible and clickable', async ({ page }) => {
    const btn = page.locator('#refreshBtn');
    await expect(btn).toBeVisible();
    await expect(btn).toHaveText('刷新数据');
    await btn.click();
    // Button should still be visible after click (no crash)
    await expect(btn).toBeVisible();
  });

  test('preset selector has 3 options', async ({ page }) => {
    const select = page.locator('#presetSelect');
    await expect(select).toBeVisible();
    const options = select.locator('option');
    await expect(options).toHaveCount(3);
    await expect(options.nth(0)).toHaveText('均衡策略');
    await expect(options.nth(1)).toHaveText('激进策略');
    await expect(options.nth(2)).toHaveText('保守策略');
  });

  test('theme toggle button exists', async ({ page }) => {
    const btn = page.locator('#themeToggle');
    await expect(btn).toBeVisible();
  });

  test('no console errors on initial load', async ({ page }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text());
      }
    });
    await page.goto('/');
    await page.waitForTimeout(2000);
    // Filter out known non-critical errors (e.g. network)
    const criticalErrors = errors.filter(
      (e) => !e.includes('WebSocket') && !e.includes('net::ERR')
    );
    expect(criticalErrors).toHaveLength(0);
  });
});
