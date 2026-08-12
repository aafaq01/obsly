import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    globals: true,
    // The SDK's whole job is talking to browser APIs, so testing it against a real DOM is the
    // only way the tests say anything about what it will actually do.
    environment: 'jsdom',
  },
})
