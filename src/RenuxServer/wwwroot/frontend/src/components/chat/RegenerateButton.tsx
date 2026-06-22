type RegenerateButtonProps = {
  disabled?: boolean
  onRegenerate: () => void
}

const RegenerateButton = ({ disabled = false, onRegenerate }: RegenerateButtonProps) => (
  <button
    type="button"
    className="regenerate-button"
    onClick={onRegenerate}
    disabled={disabled}
    aria-label="답변 다시 생성"
  >
    다시 생성
  </button>
)

export default RegenerateButton
