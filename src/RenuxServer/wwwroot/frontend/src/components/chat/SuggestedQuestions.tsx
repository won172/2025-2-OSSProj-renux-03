type SuggestedQuestionsProps = {
  questions: string[]
  disabled?: boolean
  onSelect: (q: string) => void
}

const SuggestedQuestions = ({ questions, disabled = false, onSelect }: SuggestedQuestionsProps) => {
  if (questions.length === 0) return null

  return (
    <div className="suggested-questions" aria-label="추천 질문">
      <div className="suggested-questions__heading">추천 질문</div>
      <div className="suggested-questions__list">
        {questions.map((question) => (
          <button
            key={question}
            type="button"
            className="suggested-questions__chip"
            onClick={() => onSelect(question)}
            disabled={disabled}
          >
            {question}
          </button>
        ))}
      </div>
    </div>
  )
}

export default SuggestedQuestions
