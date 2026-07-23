import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { allscanMockPlugin } from './dev/allscanMockPlugin'

// https://vite.dev/config/
const requestedBase = process.env.ASR_BASE_PATH || '/asr/'
const base = requestedBase === '/'
  ? '/'
  : `/${requestedBase.replace(/^\/+|\/+$/g, '')}/`
const proxyPrefix = base === '/' ? '/' : base.slice(0, -1)
const mockAllScan = process.env.ASR_MOCK === '1'
const asrTarget = process.env.ASR_NODE_TARGET || 'http://192.168.0.110'

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
      [proxyPrefix]: {
        target: asrTarget,
        changeOrigin: true,
      },
    },
  },
})
