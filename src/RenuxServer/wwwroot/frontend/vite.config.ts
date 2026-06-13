import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const DEV_PROXY_TARGET = env.VITE_DEV_SERVER_PROXY_TARGET || 'https://localhost:5001'

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: 'autoUpdate',
        includeAssets: ['icons/pwa-192.png', 'icons/pwa-512.png'],
        manifest: {
          name: '동국대학교 동똑이',
          short_name: '동똑이',
          description: '동국대학교 재학생 맞춤형 정보 제공 챗봇',
          lang: 'ko',
          start_url: '/',
          scope: '/',
          display: 'standalone',
          background_color: '#fff7ed',
          theme_color: '#f97316',
          icons: [
            {
              src: '/icons/pwa-192.png',
              sizes: '192x192',
              type: 'image/png',
              purpose: 'any maskable',
            },
            {
              src: '/icons/pwa-512.png',
              sizes: '512x512',
              type: 'image/png',
              purpose: 'any maskable',
            },
          ],
        },
        workbox: {
          navigateFallback: '/index.html',
          globPatterns: ['**/*.{js,css,html,png,svg,woff2,otf}'],
          runtimeCaching: [
            {
              urlPattern: ({ request }) => request.mode === 'navigate',
              handler: 'NetworkFirst',
              options: {
                cacheName: 'dongttok-pages',
                networkTimeoutSeconds: 3,
              },
            },
          ],
        },
        devOptions: {
          enabled: false,
        },
      }),
    ],
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
