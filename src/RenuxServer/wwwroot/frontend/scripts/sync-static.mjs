import { cpSync, existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

const projectRoot = resolve(__dirname, '..')
const distDir = resolve(projectRoot, 'dist')
const staticRoot = resolve(projectRoot, '..')

if (!existsSync(distDir)) {
  console.error('[sync-static] dist directory is missing. Run "vite build" first.')
  process.exit(1)
}

const entries = readdirSync(distDir, { withFileTypes: true })

if (entries.length === 0) {
  console.error('[sync-static] dist directory is empty. Abort to avoid wiping wwwroot.')
  process.exit(1)
}

for (const entry of entries) {
  const sourcePath = resolve(distDir, entry.name)
  const targetPath = resolve(staticRoot, entry.name)

  if (existsSync(targetPath)) {
    rmSync(targetPath, { recursive: true, force: true })
  }

  if (entry.isDirectory()) {
    mkdirSync(targetPath, { recursive: true })
    cpSync(sourcePath, targetPath, { recursive: true })
  } else if (entry.isFile()) {
    cpSync(sourcePath, targetPath)
  } else {
    console.warn(`[sync-static] Skip unsupported entry: ${entry.name}`)
  }
}

console.log('[sync-static] wwwroot updated with latest build output.')
