import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server on port 3000 (matches backend CORS allow-list).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    strictPort: true,
  },
})
