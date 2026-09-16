/// <reference types="vitest/config" />
import { fileURLToPath, URL } from 'node:url'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:5718',
      '/ws': { target: 'ws://127.0.0.1:5718', ws: true },
    },
  },
  test: { environment: 'jsdom', globals: true },
})
