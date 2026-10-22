/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { viteSingleFile } from 'vite-plugin-singlefile';
import { fileURLToPath, URL } from 'node:url';

// `npm run build:demo` makes one self-contained HTML file (mock data, in-memory routing)
// for sharing a clickable preview. Normal builds are a regular SPA.
export default defineConfig(({ mode }) => ({
  base: mode === 'demo' ? './' : '/',
  plugins: [react(), ...(mode === 'demo' ? [viteSingleFile()] : [])],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: { port: 5173 },
  build: mode === 'demo' ? { outDir: 'dist-demo', assetsInlineLimit: 100_000_000 } : { outDir: 'dist' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
}));
