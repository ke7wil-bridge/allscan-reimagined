import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { allscanMockPlugin } from './dev/allscanMockPlugin'

// https://vite.dev/config/
const base = process.env.ASR_BASE_PATH || '/'
const mockAllScan = process.env.ASR_MOCK === '1'
const allscanTarget = process.env.ASR_NODE_TARGET || 'http://192.168.0.110'

export default defineConfig({
  base,
  plugins: [
    react(),
    tailwindcss(),
    ...(mockAllScan ? [allscanMockPlugin()] : []),
  ],
  server: {
    host: '127.0.0.1',
    port: 4173,
    proxy: mockAllScan ? undefined : {
      '/allscan': {
        target: allscanTarget,
        changeOrigin: true,
      },
    },
  },
})
