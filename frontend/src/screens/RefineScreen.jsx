import { truncate } from '../utils'

const ACCENTS = ['brass', 'teal', 'wine']

function RefineScreen({
  memberName,
  onMemberNameChange,
  memberCandidates,
  onCandidateSelect,
  keywordSuggestions,
  onSubmit,
  onKeywordClick,
  onReset,
  loading,
  error,
  memberResolved,
}) {
  // 동명이인 후보도 없고 이미 인물이 특정된 상태(memberResolved)면, 다시 이름을
  // 입력받을 필요가 없다 — 입력창 없이 키워드 카드만 바로 보여준다.
  const showNameInput = !memberResolved || (memberCandidates && memberCandidates.length > 0)

  return (
    <div className="page is-landing">
      <button type="button" className="back-link" onClick={onReset}>
        ← 처음으로
      </button>

      <div className="refine-card">
        <div className="refine-hero">
          <div className="landing-logo small">
            의정기록<span>.</span>
          </div>
        </div>

        <form className="refine" onSubmit={onSubmit}>
          {showNameInput && (
            <>
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
            </>
          )}

          {memberCandidates && memberCandidates.length > 0 && (
            <>
              <label className="field-label">동명이인 — 찾으시는 분을 선택하세요</label>
              <div className="keyword-grid">
                {memberCandidates.map((c, i) => (
                  <button
                    type="button"
                    key={i}
                    className={`keyword-card accent-${ACCENTS[i % ACCENTS.length]}`}
                    disabled={loading}
                    onClick={() => onCandidateSelect(c)}
                  >
                    <div className="keyword-title">{c.name}</div>
                    <div className="keyword-reason">{c.party || '정당 정보 없음'}</div>
                  </button>
                ))}
              </div>
            </>
          )}

          {keywordSuggestions.length > 0 && (
            <>
              <label className="field-label">키워드 선택</label>
              <div className="keyword-grid">
                {keywordSuggestions.map((kw, i) => (
                  <button
                    type="button"
                    key={i}
                    className={`keyword-card accent-${ACCENTS[i % ACCENTS.length]}`}
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
