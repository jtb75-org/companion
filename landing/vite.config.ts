import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Standalone static marketing build. Output is plain, crawlable static files
// (see index.html for the SEO head). This package is intentionally decoupled
// from web/ — it shares no bundle, no auth, and touches no PHI.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
  },
  build: {
    // Never ship source maps to prod (mirrors web/vite.config.ts).
    sourcemap: false,
  },
})
