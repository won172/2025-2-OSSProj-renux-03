import type { CapacitorConfig } from '@capacitor/cli'
import { KeyboardResize } from '@capacitor/keyboard'

const config: CapacitorConfig = {
  appId: 'com.wonmac.dongttok',
  appName: '동똑이',
  webDir: 'dist',
  ios: {
    // The web UI owns safe-area padding via env(safe-area-inset-*).
    // Avoid a second UIKit inset when the keyboard closes or the device rotates.
    contentInset: 'never',
    limitsNavigationsToAppBoundDomains: true,
    preferredContentMode: 'mobile',
  },
  plugins: {
    Keyboard: {
      // Resize the viewport so the chat shell's 100dvh follows the keyboard.
      // Do not also subtract keyboard height or translate the composer in JS/CSS.
      resize: KeyboardResize.Native,
    },
    SplashScreen: {
      launchAutoHide: true,
      launchShowDuration: 800,
      backgroundColor: '#fff7ed',
      showSpinner: false,
    },
  },
}

export default config
