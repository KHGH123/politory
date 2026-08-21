const SOURCE_LABEL = {
  primary: '1차 · 회의록 원문',
  secondary: '2차 · 뉴스 보도',
}

// answer 문장 끝의 [1], [2] 각주를 클릭 가능한 링크로 바꾼다. merge가
// sources 배열 순서(1-based)와 번호를 맞춰 붙이도록 지시받았으므로, 여기서는
// 그 번호를 그대로 #source-{n} 앵커로 연결해 타임라인 카드로 스크롤만 시킨다.
// url이 없는 source는 타임라인에서 숨기므로(action_agent hallucination 완화),
// 그런 번호를 가리키는 각주는 클릭해도 갈 곳이 없다 — 그런 번호는 눌러도
// 반응 없는 버튼 대신 그냥 일반 텍스트로 표시한다.
function renderAnswerWithFootnotes(answer, onFootnoteClick, visibleIndexSet) {
  const parts = answer.split(/(\[\d+\])/g)
  return parts.map((part, i) => {
    const match = part.match(/^\[(\d+)\]$/)
    if (!match) return part
    const index = Number(match[1])
    if (!visibleIndexSet.has(index)) return part
    return (
      <button
        type="button"
        key={i}
        className="footnote"
        onClick={() => onFootnoteClick(index)}
        aria-label={`${index}번 출처로 이동`}
      >
        {part}
      </button>
    )
  })
}

function ResultsScreen({ question, memberName, result, onReset }) {
  // url 있는 source의 1-based 인덱스만 "클릭 가능한 각주"로 취급한다.
  const visibleIndexSet = new Set(
    (result?.sources || []).flatMap((s, i) => (s.url ? [i + 1] : []))
  )

  function scrollToSource(index) {
    // 각주는 1-based, sources 배열 순서와 동일하다고 merge가 보장한다.
    const el = document.getElementById(`source-${index}`)
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('t-card-highlight')
    window.setTimeout(() => el.classList.remove('t-card-highlight'), 1200)
  }

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
            {renderAnswerWithFootnotes(result.answer, scrollToSource, visibleIndexSet)}
            <div className="disclaimer">
              ⚠ 이 답변은 원문 검색 결과를 시간순으로 정리한 것이며, 해석적 판단(입장 변화 등)을 포함하지 않습니다.
              최종 판단은 원문을 직접 확인해주세요.
            </div>
          </div>

          {result.sources && result.sources.filter((s) => s.url).length > 0 && (
            <>
              <div className="section-label" style={{ marginTop: 44 }}>
                Timeline
              </div>
              <div className="section-title">발언 · 출처</div>
              <div className="timeline">
                {result.sources.map((source, i) => {
                  // url 없는 항목은 화면에서 숨긴다 — action_agent가 아직 tools
                  // 미연결(hallucination 가능) 상태라, url 없는 근거는 실제
                  // 검색된 문서가 아니라 지어낸 내용일 가능성이 높다. 각주 번호는
                  // answer가 sources의 원래 1-based 인덱스를 참조하므로, 숨기더라도
                  // 번호(i + 1)는 그대로 유지해야 [1], [4] 같은 참조가 깨지지 않는다.
                  if (!source.url) return null
                  return (
                    <div className="t-item" key={i}>
                      {source.date && <div className="t-date">{source.date}</div>}
                      <div className="t-card" id={`source-${i + 1}`}>
                        <div className="t-index">[{i + 1}]</div>
                        <div className="t-quote">{source.title}</div>
                        {source.description && (
                          <div className="t-summary">{source.description}</div>
                        )}
                        {source.excerpt && (
                          <div className="t-excerpt">{source.excerpt}</div>
                        )}
                        <div className="t-footer">
                          <a className="t-source" href={source.url} target="_blank" rel="noreferrer">
                            → 원문 보기
                          </a>
                          <span className={`source-tag ${source.type === 'primary' ? 'primary' : 'secondary'}`}>
                            {SOURCE_LABEL[source.type] || '출처'}
                          </span>
                        </div>
                      </div>
                    </div>
                  )
                })}
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
