import { truncate } from '../utils'

function RefineScreen({
  memberName,
  onMemberNameChange,
  keywordSuggestions,
  onSubmit,
  onKeywordClick,
  onReset,
  loading,
  error,
}) {
  return (
    <div className="page is-landing">
      <div className="refine-card">
        <div className="refine-hero">
          <button type="button" className="landing-logo small logo-link" onClick={onReset}>
            의정기록<span>.</span>
          </button>
        </div>

        <form className="refine" onSubmit={onSubmit}>
          <label className="field-label" htmlFor="refine-target">
            특정인 / 정책
          </label>
          <input
            id="refine-target"
            type="text"
            className="refine-field"
            placeholder="예: 김OO 의원"
            value={memberName}
            onChange={(e) => onMemberNameChange(e.target.value)}
            disabled={loading}
            autoFocus
          />

          {keywordSuggestions.length > 0 && (
            <>
              <label className="field-label">키워드 선택</label>
              <div className="keyword-grid">
                {keywordSuggestions.map((kw, i) => (
                  <button
                    type="button"
                    key={i}
                    className="keyword-card"
                    disabled={loading}
                    onClick={() => onKeywordClick(kw.title)}
                  >
                    <div className="keyword-title">{kw.title}</div>
                    <div className="keyword-reason">{truncate(kw.reason, 40)}</div>
                  </button>
                ))}
              </div>
            </>
          )}
        </form>

        {loading && <p className="hint">조회 중...</p>}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  )
}

export default RefineScreen
