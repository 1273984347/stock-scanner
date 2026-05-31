import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './',
  timeout: 60000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:8000',
    headless: true,
    viewport: { width: 1280, height: 720 },
    actionTimeout: 10000,
    ignoreHTTPSErrors: true,
  },
  reporter: [['list']],
});
