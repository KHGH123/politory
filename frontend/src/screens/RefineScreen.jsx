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
}) {
  return (
    <div className="page is-landing">
      <button type="button" className="back-link" onClick={onReset}>
        ← 처음으로
      </button>

      <div className="refine-card">
        <div className="refine-hero">
          <div className="landing-logo small">
            Politory<span>.</span>
          </div>
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

          {memberCandidates && memberCandidates.length > 0 && (
            <>
              <label className="field-label">
                {keywordSuggestions.length > 0
                  ? '동명이인 — 찾으시는 분을 선택하세요'
                  : '관련 의원 — 조회할 분을 선택하세요'}
              </label>
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

          {/* 동명이인 후보가 남아있는 동안은 키워드부터 고를 수 없게 숨긴다 —
              인물이 특정 안 된 채로 키워드를 누르면 member_name 없이 조회가
              나가 화면3 약력 카드가 비는 버그로 이어진다(먼저 위 후보 카드로
              인물을 확정해야 이 블록이 뜬다). */}
          {(!memberCandidates || memberCandidates.length === 0) && keywordSuggestions.length > 0 && (
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

          {/* 후보도 키워드도 하나도 없으면 백엔드(classify)가 이 이름을 등록된
              국회의원으로도, 관련 상임위 인물로도 특정하지 못했다는 뜻이다
              (예: 정치인이 아닌 이름). 빈 화면만 보여주면 사용자가 뭐가
              잘못됐는지 알 수 없으니 명시적으로 안내한다. */}
          {(!memberCandidates || memberCandidates.length === 0) &&
            keywordSuggestions.length === 0 &&
            !loading && (
              <p className="hint">
                등록된 국회의원을 찾지 못했습니다. 이름이나 표현을 다시 확인해주세요.
              </p>
            )}
        </form>

        {loading && <p className="hint">조회 중...</p>}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  )
}

export default RefineScreen
