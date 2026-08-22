function LandingScreen({ question, onQuestionChange, onSubmit, loading, error }) {
  return (
    <div className="page is-landing">
      <div className="landing-hero">
        <div className="landing-logo">
          의정기록<span>.</span>
        </div>
      </div>

      <form className="searchbar" onSubmit={onSubmit}>
        <div className="search-box">
          <svg className="search-icon" width="18" height="18" viewBox="0 0 24 24" fill="none">
            <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
          <input
            type="text"
            placeholder="인물 또는 정책 입력"
            value={question}
            onChange={(e) => onQuestionChange(e.target.value)}
            disabled={loading}
            autoFocus
          />
        </div>
      </form>

      {loading && <p className="hint">확인 중...</p>}
      {error && <p className="error">{error}</p>}
    </div>
  )
}

export default LandingScreen
