import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const DEV_PROXY_TARGET = env.VITE_DEV_SERVER_PROXY_TARGET || 'https://localhost:5001'
  const isNativeBuild = mode === 'native'
  const nativeApiBaseUrl = env.VITE_API_BASE_URL?.trim()

  if (isNativeBuild && (!nativeApiBaseUrl || !nativeApiBaseUrl.startsWith('https://'))) {
    throw new Error('Native builds require VITE_API_BASE_URL with a stable https:// API origin.')
  }

  return {
    plugins: [
      react(),
      ...(!isNativeBuild ? [VitePWA({
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
      })] : []),
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
          // 관리자 콘솔은 화면 경로(/admin/review)와 API 경로(/admin/items)가 같은 접두사를 쓴다.
          // 주소창으로 직접 들어온 화면 요청까지 백엔드로 넘기면 SPA가 뜨지 않으므로,
          // HTML을 원하는 내비게이션 요청만 index.html로 돌려 라우터가 처리하게 한다.
          // (운영 환경은 ASP.NET의 MapFallbackToFile이 같은 역할을 한다.)
          bypass: (req) => {
            const accept = req.headers.accept ?? ''
            if (req.method === 'GET' && accept.includes('text/html')) {
              return '/index.html'
            }
            return undefined
          },
        },
        '/notifications': {
          target: DEV_PROXY_TARGET,
          changeOrigin: true,
          secure: false,
        },
        '/home': {
          target: DEV_PROXY_TARGET,
          changeOrigin: true,
          secure: false,
        },
      },
    },
  }
})
