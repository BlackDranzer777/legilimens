import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // Relative asset paths so the built app works when the backend serves it
  // from http://localhost:4436/ inside the packaged Electron shell.
  base: './',
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5180,
    strictPort: true,
    cors: { origin: ['http://127.0.0.1:5180', 'http://localhost:5180'] },
  },
})
