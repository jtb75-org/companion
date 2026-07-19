import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  build: {
    // Never ship source maps to prod — they let an attacker reconstruct the frontend
    // source. This is already Vite's default; pin it explicitly so a future config
    // change or a stray --sourcemap flag can't silently expose them.
    sourcemap: false,
  },
})
