/// <reference types="vitest/config" />

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const CORE = process.env.JARVIS_CORE_URL ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Plugin frontends live above frontend/, where the node_modules walk-up
    // from the importing file ends before finding anything. These are pinned
    // to the app's own copies so plugin code shares one React.
    dedupe: ['react', 'react-dom', 'react-router'],
  },
  test: {
    // Plugin frontends live outside this package (../plugin/*/frontend), so
    // the default include would never see their tests. Everything is pure
    // functions today; widen the suffixes if a .tsx test ever shows up.
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx', '../plugin/*/frontend/**/*.test.ts'],
  },
  server: {
    // Bind IPv4 explicitly. The default `localhost` resolves to ::1 on Windows,
    // and anything reaching for 127.0.0.1 - which is what the backend uses -
    // then gets a connection refused for no visible reason.
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    // Same origin as the built app, which the backend serves from `/`. That
    // keeps the client code identical in dev and in production, and sidesteps
    // CORS entirely.
    proxy: {
      '/api': { target: CORE, changeOrigin: false },
    },
  },
})
