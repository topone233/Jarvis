import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const CORE = process.env.JARVIS_CORE_URL ?? 'http://127.0.0.1:8787'

export default defineConfig({
  plugins: [react()],
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
