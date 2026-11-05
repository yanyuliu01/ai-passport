import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
export default defineConfig({ root: fileURLToPath(new URL('.', import.meta.url)),
  base: './', build: { outDir: '/private/tmp/xiaoyu-weixin-test-ui', emptyOutDir: true } });
