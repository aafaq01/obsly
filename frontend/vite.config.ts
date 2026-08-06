import react from '@vitejs/plugin-react'
// vitest/config re-exports vite's defineConfig with the `test` key typed in.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    // Same-origin in the browser, so no CORS configuration is needed in development.
    proxy: {
      '/health': 'http://localhost:8000',
      '/api': 'http://localhost:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
    css: false,
  },
})
