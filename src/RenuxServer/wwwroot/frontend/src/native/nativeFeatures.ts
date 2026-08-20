import { Capacitor } from '@capacitor/core'
import { Haptics, NotificationType } from '@capacitor/haptics'
import { Share } from '@capacitor/share'

export const canShareAnswer = () =>
  Capacitor.isNativePlatform() || typeof navigator.share === 'function'

export const shareAnswer = async (text: string) => {
  if (Capacitor.isNativePlatform()) {
    await Share.share({
      title: '동똑이 답변',
      text,
      dialogTitle: '동똑이 답변 공유',
    })
    return
  }

  if (typeof navigator.share === 'function') {
    await navigator.share({ title: '동똑이 답변', text })
    return
  }

  throw new Error('Share is not available on this platform.')
}

export const signalAnswerCompleted = async () => {
  if (!Capacitor.isNativePlatform()) return
  try {
    await Haptics.notification({ type: NotificationType.Success })
  } catch {
    // 햅틱을 지원하지 않는 기기에서도 답변 완료 자체는 정상 처리한다.
  }
}
