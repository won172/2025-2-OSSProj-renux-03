import { useEffect, useRef, useState } from 'react'

type CopyButtonProps = {
  text: string
}

const CopyButton = ({ text }: CopyButtonProps) => {
  const [copied, setCopied] = useState(false)
  const timeoutRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current)
      }
      timeoutRef.current = window.setTimeout(() => {
        setCopied(false)
        timeoutRef.current = null
      }, 1500)
    } catch (error) {
      console.warn('Failed to copy message text', error)
    }
  }

  return (
    <button
      type="button"
      className={`copy-button ${copied ? 'copy-button--copied' : ''}`}
      onClick={handleCopy}
      aria-label={copied ? '답변 복사됨' : '답변 복사'}
    >
      {copied ? '복사됨' : '복사'}
    </button>
  )
}

export default CopyButton
