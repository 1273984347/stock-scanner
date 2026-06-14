import { defineConfig } from 'vite';
import path from 'path';
import { fileURLToPath } from 'url';
import { readFileSync } from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// 读取统一端口配置
const ports = JSON.parse(readFileSync(path.resolve(__dirname, 'ports.json'), 'utf-8'));

export default defineConfig({
  root: 'static',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    target: 'esnext',
  },
  server: {
    port: ports.frontend,
    proxy: {
      '/api': `http://localhost:${ports.backend}`,
      '/ws': {
        target: `ws://localhost:${ports.backend}`,
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'static'),
    },
  },
});
