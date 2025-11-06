import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const DEV_PROXY_TARGET = process.env.VITE_DEV_SERVER_PROXY_TARGET ?? 'https://localhost:5001'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/auth': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
      },
      '/req': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
      },
      '/chat': {
        target: DEV_PROXY_TARGET,
        changeOrigin: true,
        secure: false,
      },
    },
  },
})
