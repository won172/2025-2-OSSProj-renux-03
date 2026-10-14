const RELOAD_KEY = 'dongttok-module-recovery-attempted'

/** Only module transport failures qualify. Rendering, API and ordinary network errors do not. */
export const isModuleLoadError = (error: unknown) => error instanceof Error && (
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed/i.test(error.message)
  || (error.name === 'ChunkLoadError' && /Loading chunk .+ failed/i.test(error.message))
)

export const recoverModuleLoad = (error: unknown, storage: Pick<Storage, 'getItem' | 'setItem'>, reload: () => void, online = true) => {
  if (!online || !isModuleLoadError(error)) return false
  try {
    // Never reset on mount/success: another stale chunk must not start a reload loop.
    // If storage is unavailable, prefer the friendly manual recovery screen.
    if (storage.getItem(RELOAD_KEY)) return false
    storage.setItem(RELOAD_KEY, '1')
  } catch {
    return false
  }
  reload()
  return true
}

export const importWithRecovery = async <T,>(load: () => Promise<T>): Promise<T> => {
  try {
    return await load()
  } catch (error) {
    if (typeof window !== 'undefined') {
      try {
        recoverModuleLoad(error, window.sessionStorage, () => window.location.reload(), window.navigator.onLine)
      } catch {
        // Storage access itself can throw. Preserve the original failure for the boundary.
      }
    }
    throw error
  }
}
