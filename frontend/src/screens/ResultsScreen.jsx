import { useState } from 'react'

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

// 명함형 카드 우측에 지역구/위원회/선수·대수를 라벨과 함께 한 줄씩 보여준다.
// 값이 없는 줄은 아예 표시하지 않는다.
function buildProfileRows(profile) {
  if (!profile) return []
  const termStatus = [
    profile.term_count ? `${profile.term_count}선` : null,
    profile.status,
  ]
    .filter(Boolean)
    .join(' · ')
  return [
    { label: '지역구', value: profile.district },
    { label: '위원회', value: profile.committee },
    { label: '선수', value: termStatus },
  ].filter((row) => row.value)
}

function ResultsScreen({ question, result, onReset }) {
  const profile = result?.member_profile
  const profileRows = buildProfileRows(profile)
  const [timelineDesc, setTimelineDesc] = useState(false)
  // url 있는 source의 1-based 인덱스만 "클릭 가능한 각주"로 취급한다.
  const visibleIndexSet = new Set(
    (result?.sources || []).flatMap((s, i) => (s.url ? [i + 1] : []))
  )
  // 백엔드가 이미 과거->최신 순으로 정렬해서 준다. 각주 [1][2]는 이 원래
  // 순서(1-based)를 가리키므로, 정렬을 바꿔도 originalIndex는 그대로 보존해
  // id="source-{n}"와 각주 번호가 화면 표시 순서와 무관하게 유지되게 한다.
  const indexedSources = (result?.sources || []).map((source, i) => ({
    source,
    originalIndex: i,
  }))
  const displaySources = timelineDesc ? [...indexedSources].reverse() : indexedSources

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
          Politory<span>.</span>
        </div>
        <div className="tag">국회의원 통합 의정활동 조회</div>
      </div>

      {result && (
        <>
          {/* profile은 /api/query가 BigQuery mps 테이블에서 실제로 조회에
              성공했을 때만 채워진다(member_name이 DB에 없으면 백엔드가 애초에
              404를 낸다). memberName(화면 입력 텍스트)을 폴백으로 같이 쓰면
              등록되지 않은 이름(예: "윤석열")도 그럴듯한 인물 카드처럼
              보여버리는 문제가 있었다 — 검색축은 반드시 등록된 국회의원이어야
              한다는 원칙에 따라 실제 조회된 profile이 있을 때만 카드를 그린다. */}
          {profile && (
            <div className="profile-card profile-card-id">
              <div className="profile-id-block">
                <div className="profile-photo profile-photo-large" aria-hidden="true">
                  {profile.image_url ? (
                    <img src={profile.image_url} alt="" />
                  ) : (
                    <svg width="40" height="40" viewBox="0 0 24 24" fill="none">
                      <circle cx="12" cy="8" r="4" stroke="currentColor" strokeWidth="1.6" />
                      <path
                        d="M4 20c0-4 3.6-6.5 8-6.5s8 2.5 8 6.5"
                        stroke="currentColor"
                        strokeWidth="1.6"
                        strokeLinecap="round"
                      />
                    </svg>
                  )}
                </div>
                <div className="profile-name">{profile.name}</div>
                <div className="profile-party">{profile.party || '정당 정보 없음'}</div>
              </div>

              <div className="profile-detail-rows">
                {profileRows.length > 0 ? (
                  profileRows.map((row, i) => (
                    <div className="profile-detail-row" key={i}>
                      <span className="profile-detail-label">{row.label}</span>
                      <span className="profile-detail-value">{row.value}</span>
                    </div>
                  ))
                ) : (
                  <div className="profile-detail-row">약력 정보 준비 중 · 국회 공공데이터 연동 예정</div>
                )}
              </div>
            </div>
          )}

          <div className="section-label">RAG Summary</div>
          <div className="section-title">답변</div>
          <div className="ai-answer">
            <div className="q">Q. {question}</div>
            {renderAnswerWithFootnotes(result.answer, scrollToSource, visibleIndexSet)}
            <div className="disclaimer">
              ⚠ 이 답변은 해석적 판단(입장 변화 등)을 포함하지 않습니다. 최종 판단은 원문을 직접 확인해주세요.
            </div>
          </div>

          {result.sources && result.sources.filter((s) => s.url).length > 0 && (
            <>
              <div
                className="section-label"
                style={{ marginTop: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
              >
                <span>Timeline</span>
                <button
                  type="button"
                  className="sort-toggle"
                  onClick={() => setTimelineDesc((prev) => !prev)}
                >
                  {timelineDesc ? '최신순 ↓' : '오래된순 ↑'}
                </button>
              </div>
              <div className="section-title">발언 · 출처</div>
              <div className="timeline">
                {displaySources.map(({ source, originalIndex }) => {
                  // url 없는 항목은 화면에서 숨긴다 — action_agent가 아직 tools
                  // 미연결(hallucination 가능) 상태라, url 없는 근거는 실제
                  // 검색된 문서가 아니라 지어낸 내용일 가능성이 높다. 각주 번호는
                  // answer가 sources의 원래 1-based 인덱스를 참조하므로, 정렬
                  // 순서를 바꾸거나 숨기더라도 번호(originalIndex + 1)는 그대로
                  // 유지해야 [1], [4] 같은 참조가 깨지지 않는다.
                  if (!source.url) return null
                  return (
                    <div className="t-item" key={originalIndex}>
                      {source.date && <div className="t-date">{source.date}</div>}
                      <div className="t-card" id={`source-${originalIndex + 1}`}>
                        <div className="t-index">[{originalIndex + 1}]</div>
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

      <footer>Politory · 국회 공공데이터 기반 프로토타입</footer>
    </div>
  )
}

export default ResultsScreen
