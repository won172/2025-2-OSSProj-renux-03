import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const DEV_PROXY_TARGET = env.VITE_DEV_SERVER_PROXY_TARGET || 'https://localhost:5001'

  return {
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
        '/admin': {
          target: DEV_PROXY_TARGET,
          changeOrigin: true,
          secure: false,
        },
      },
    },
  }
})
