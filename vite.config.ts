import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
const base = process.env.ASR_BASE_PATH || '/'

export default defineConfig({
  base,
  plugins: [react(), tailwindcss()],
  server: {
    host: '127.0.0.1',
    port: 4173,
    proxy: {
      '/allscan': {
        target: 'http://192.168.0.110',
        changeOrigin: true,
      },
    },
  },
})
