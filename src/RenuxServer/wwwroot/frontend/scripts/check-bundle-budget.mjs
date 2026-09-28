import { readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// Baseline (2026-09-28): main JS 237,623 B; precache 2,672,877 B (37 unique URLs).
// Excluding the original logo, font, and admin-only assets leaves room below 1.25 MB.
const MAIN_JS_MAX_BYTES = 240_000
const PRECACHE_MAX_BYTES = 1_250_000

const adminAsset = /^assets\/(?:admin[^/]*|Admin[^/]*|DashboardPage|ReviewPage|ContentPage|ChatLogPage|FeedbackPage|UsersPage|SystemPage|DepartmentAdminPage|UniversityOnly|TrendChart|QuickFaqModal|Modal|ui|format)-[^/]+\.(?:js|css)$/
const nonCriticalAsset = /^assets\/dongddoki-logo-[^/]+\.png$|\.(?:otf|ttf|woff2)$/

export function checkBundleBudget(distDir = join(projectRoot, 'dist')) {
  const html = readFileSync(join(distDir, 'index.html'), 'utf8')
  const mainJs = html.match(/<script[^>]+src="\/(assets\/index-[^"]+\.js)"/)?.[1]
  if (!mainJs) throw new Error('[bundle-budget] Cannot find the main JS entry in dist/index.html.')

  const sw = readFileSync(join(distDir, 'sw.js'), 'utf8')
  const manifestEntries = [...sw.matchAll(/\{url:"([^"]+)",revision:(?:"[^"]+"|null)\}/g)]
  if (manifestEntries.length === 0) {
    throw new Error('[bundle-budget] Cannot read the precache manifest from dist/sw.js.')
  }

  // VitePWA can repeat includeAssets URLs in the generated manifest. Count each download once.
  const urls = [...new Set(manifestEntries.map((entry) => entry[1]))]
  const precacheBytes = urls.reduce((total, url) => total + statSync(join(distDir, url)).size, 0)
  const mainJsBytes = statSync(join(distDir, mainJs)).size
  const forbidden = urls.filter((url) => adminAsset.test(url) || nonCriticalAsset.test(url))

  console.log(`[bundle-budget] precache: ${precacheBytes} B / ${PRECACHE_MAX_BYTES} B (${manifestEntries.length} entries, ${urls.length} unique URLs)`)
  console.log(`[bundle-budget] main JS: ${mainJsBytes} B / ${MAIN_JS_MAX_BYTES} B (${mainJs})`)

  if (!urls.includes('index.html') || !urls.includes(mainJs)) {
    throw new Error('[bundle-budget] The app shell or main JS is missing from precache.')
  }
  if (forbidden.length > 0) {
    throw new Error(`[bundle-budget] Non-critical assets were precached: ${forbidden.join(', ')}`)
  }
  if (precacheBytes > PRECACHE_MAX_BYTES || mainJsBytes > MAIN_JS_MAX_BYTES) {
    throw new Error('[bundle-budget] Build exceeds the precache or main JS budget.')
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  checkBundleBudget(process.argv[2] ? resolve(process.argv[2]) : undefined)
}
