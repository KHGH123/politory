const SOURCE_LABEL = {
  primary: '1차 · 회의록 원문',
  secondary: '2차 · 뉴스 보도',
}

function ResultsScreen({ question, memberName, result, onReset }) {
  return (
    <div className="page is-results">
      <button type="button" className="back-link" onClick={onReset}>
        ← 처음으로
      </button>

      <div className="masthead">
        <div className="logo">
          의정기록<span>.</span>
        </div>
        <div className="tag">국회의원 통합 의정활동 조회</div>
      </div>

      {result && (
        <>
          {memberName && (
            <div className="profile-card">
              <div className="profile-photo" aria-hidden="true">
                <svg width="34" height="34" viewBox="0 0 24 24" fill="none">
                  <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.6" />
                  <path
                    d="M4 20c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                  />
                </svg>
              </div>
              <div className="profile-body">
                <div className="profile-name">{memberName}</div>
                <div className="profile-bio">약력 정보 준비 중 · 국회 공공데이터 연동 예정</div>
              </div>
            </div>
          )}

          <div className="section-label">RAG Summary</div>
          <div className="section-title">답변</div>
          <div className="ai-answer">
            <div className="q">Q. {question}</div>
            {result.answer}
            <div className="disclaimer">
              ⚠ 이 답변은 원문 검색 결과를 시간순으로 정리한 것이며, 해석적 판단(입장 변화 등)을 포함하지 않습니다.
              최종 판단은 원문을 직접 확인해주세요.
            </div>
          </div>

          {result.sources && result.sources.length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: 44 }}>
                Timeline
              </div>
              <div className="section-title">발언 · 출처</div>
              <div className="timeline">
                {result.sources.map((source, i) => (
                  <div className="t-item" key={i}>
                    {source.date && <div className="t-date">{source.date}</div>}
                    <div className="t-card">
                      <div className="t-quote">{source.title}</div>
                      <div className="t-footer">
                        {source.url ? (
                          <a className="t-source" href={source.url} target="_blank" rel="noreferrer">
                            → 원문 보기
                          </a>
                        ) : (
                          <span />
                        )}
                        <span className={`source-tag ${source.type === 'primary' ? 'primary' : 'secondary'}`}>
                          {SOURCE_LABEL[source.type] || '출처'}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}

      <footer>의정기록 · 국회 공공데이터 기반 프로토타입</footer>
    </div>
  )
}

export default ResultsScreen
