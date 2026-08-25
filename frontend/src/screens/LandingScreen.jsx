function LandingScreen({ question, onQuestionChange, onSubmit, loading, error }) {
  return (
    <div className="page is-landing">
      <div className="landing-hero">
        <div className="landing-logo">
          Politory<span>.</span>
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

      {/* RefineScreen의 진행 배지(.hint-badge/.hint-dots, App.css)와 같은
          스타일을 재사용한다 — 여기는 /api/classify 단일 호출이라 로그로
          쌓을 단계가 없으므로 배지 하나만 보여준다. data-stage 없이 두면
          기본 종이색·남색 톤으로 표시된다. */}
      {loading && (
        <p className="hint">
          <span className="hint-badge">
            <span className="hint-dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span className="hint-text">질문 확인 중</span>
          </span>
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  )
}

export default LandingScreen
