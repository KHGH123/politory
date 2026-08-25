import {
  truncate,
  progressStageOf,
  PROGRESS_STEPS,
  computeStepStatuses,
  splitProgressLog,
  isCompleteLabel,
} from '../utils'

// fetch 레인(action/speech/context) 표시용 순서·라벨. PROGRESS_STEPS의
// stageLabels와 같은 이름을 쓰되, 레인 UI 전용이라 여기 따로 둔다.
const FETCH_LANES = [
  { key: 'action', label: '법안·표결' },
  { key: 'speech', label: '회의록' },
  { key: 'context', label: '뉴스' },
]

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
  progressLog,
  error,
}) {
  const stepStatuses = loading ? computeStepStatuses(progressLog) : null
  const { before: beforeLog, after: afterLog, lanes } = loading
    ? splitProgressLog(progressLog)
    : { before: [], after: [], lanes: { action: [], speech: [], context: [] } }

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
                    key={c.legislator_id || i}
                    className={`keyword-card accent-${ACCENTS[i % ACCENTS.length]}`}
                    disabled={loading}
                    onClick={() => onCandidateSelect(c)}
                  >
                    <div className="keyword-title">{c.name}</div>
                    <div className="keyword-reason">{c.party || '정당 정보 없음'}</div>
                    {c.district && <div className="keyword-reason">{c.district}</div>}
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
        </form>

        {/* 파이프라인 구조(agent/agent.py: query_processing -> fetch(병렬
            action/speech/context) -> merge -> guardrail)를 그대로 5칸
            트래커로 보여준다 — "에이전트 구조가 좀 더 시각화됐으면"이라는
            피드백. 자료 조회 칸은 3개 상태를 색상 원으로 그려 "여기가 동시에
            돈다"는 걸 직관적으로 전달한다(실제로 ParallelAgent). 각 칸의
            상태(대기/진행중/완료)는 computeStepStatuses(utils.js)가
            progressLog 하나로부터 계산한다 — 아래 로그와 상태 계산 근거는
            같지만, 트래커는 "전체 중 어디쯤"을, 로그는 "지금 뭘 하는지"를
            보여줘 서로 보완한다. */}
        {loading && stepStatuses && (
          <ol className="stepper">
            {PROGRESS_STEPS.map((step) => {
              const status = stepStatuses[step.key]
              const overall = step.parallel ? status.overall : status
              return (
                <li key={step.key} className={`stepper-node status-${overall}${step.parallel ? ' is-parallel' : ''}`}>
                  <span className="stepper-dot" aria-hidden="true" />
                  {step.parallel ? (
                    <span className="stepper-parallel">
                      <span className="stepper-label">{step.label}</span>
                      <span className="stepper-chips">
                        {step.stages.map((s) => (
                          <span
                            key={s}
                            className={`stepper-chip status-${status.sub[s]}`}
                            data-stage={s}
                            title={step.stageLabels[s]}
                            aria-label={`${step.stageLabels[s]}: ${status.sub[s]}`}
                          >
                            {step.stageLabels[s]}
                          </span>
                        ))}
                      </span>
                    </span>
                  ) : (
                    <span className="stepper-label">{step.label}</span>
                  )}
                </li>
              )
            })}
          </ol>
        )}

        {/* progressLog는 /api/query/stream이 보낸 진행 문구를 도착한 순서대로
            쌓은 배열이다(App.jsx의 runQuery — 연속 중복은 한 줄로 합침).
            fetch 단계(법안·표결/회의록/뉴스)는 실제로 ParallelAgent라 동시에
            진행되는데, 그냥 한 줄 로그로만 보여주면 도착 순서로만 읽혀서
            "셋이 동시에 돈다"는 게 안 드러난다 — "병렬 처리니까 그거에 맞게
            로그"라는 피드백. splitProgressLog(utils.js)가 실제 실행 순서
            (질문 분석 -> 병렬 조회 -> 근거 종합 -> 답변 검증) 그대로 화면
            블록도 beforeLog(질문 분석) -> 레인 그리드(병렬 3갈래) ->
            afterLog(근거 종합/답변 검증) 순서로 배치한다 — "병합 단계가
            아래에 나오도록"이라는 피드백. 이전엔 순차 단계를 하나로 묶어
            레인 위에 다 몰아넣어서, 아직 시작도 안 한 근거 종합/답변 검증이
            화면상 병렬 레인보다 먼저 보이는 게 실제 실행 순서와 어긋났다.

            loading이 켜진 직후 ~ 첫 progress 이벤트가 도착하기 전(요청
            전송 + query_processing 첫 LLM 호출, 실측 4~7초) 사이에는
            progressLog가 아직 빈 배열이라 화면에 아무것도 안 뜨는 구간이
            있었다 — "몇 초간 렉 걸린 것처럼 아무것도 안 보인다"는 피드백.
            그 구간을 커버하는 자리표시 배지를 진짜 로그(progressLog)와
            별개로 항상 먼저 보여준다. */}
        {loading && progressLog.length === 0 && (
          <ul className="hint-log">
            <li className="hint-badge">
              <span className="hint-dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <span className="hint-text">요청 준비 중</span>
            </li>
          </ul>
        )}

        {loading && beforeLog.length > 0 && (
          <ul className="hint-log">
            {beforeLog.map((label, i) => {
              // beforeLog는 "질문 분석 중" -> "질문 분석 완료" 최대 두 줄이다.
              // 펄스 점(진행 중 표시)은 마지막 줄이면서 착수 문구일 때만
              // 붙인다 — 붙이지 말아야 할 두 경우를 각각 실제 피드백으로
              // 발견했다: (1) fetch(병렬 레인)가 이미 시작됐는데도 "질문
              // 분석 중"에 계속 펄스가 남아 있던 문제("법안·표결 수집 중인데
              // 질문 분석 중도 같이 활성화됐다") -> fetchStarted로 방지.
              // (2) "질문 분석 완료"처럼 문구 자체가 끝났다고 말하는데
              // 옆에서 점이 깜빡이면 모순돼 보이는 문제 -> isCompleteLabel로
              // 방지.
              const fetchStarted = Object.values(lanes).some((entries) => entries.length > 0)
              const isLast = i === beforeLog.length - 1 && !fetchStarted && !isCompleteLabel(label)
              return (
                <li
                  key={`before-${i}-${label}`}
                  className={`hint-badge${isLast ? '' : ' is-past'}`}
                  data-stage={progressStageOf(label)}
                >
                  {isLast && (
                    <span className="hint-dots" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </span>
                  )}
                  <span className="hint-text">{label}</span>
                </li>
              )
            })}
          </ul>
        )}

        {/* 병렬 레인 3개를 나란히(그리드) 배치한다. 각 레인 안에서는 지금까지
            쌓인 로그를 세로로 쌓되(같은 hint-badge 스타일 재사용), 레인
            자체가 옆으로 나열돼 있어 "이 셋이 동시에 실행된다"는 걸 굳이
            설명하지 않아도 레이아웃으로 전달된다. 아직 아무 이벤트도 없는
            레인은 빈 채로 두지 않고 "대기 중" 자리표시를 보여줘 세 칸이
            항상 나란히 보이게 한다(하나만 먼저 나타나면 병렬이라는 인상이
            오히려 깨진다). */}
        {loading && beforeLog.length > 0 && (
          <div className="lane-grid">
            {FETCH_LANES.map(({ key, label }) => (
              <div key={key} className="lane" data-stage={key}>
                <div className="lane-label">{label}</div>
                <ul className="hint-log lane-log">
                  {lanes[key].length === 0 ? (
                    <li className="hint-badge is-waiting">
                      <span className="hint-text">대기 중</span>
                    </li>
                  ) : (
                    lanes[key].map((entryLabel, i) => {
                      // 레인의 마지막 줄이어도, (1) 근거 종합/답변 검증이
                      // 이미 시작됐거나(afterLog가 채워짐 — fetch 전체가
                      // 끝났다는 뜻, "병합할 때 그 이전이 아직 실행 중이던데"
                      // 라는 피드백) (2) 그 줄 자체가 완료 문구("N건 확인"
                      // 등)면 펄스를 붙이지 않는다 — 둘 다 "이 레인은 이미
                      // 끝났다"는 신호다.
                      const isLast =
                        i === lanes[key].length - 1 &&
                        afterLog.length === 0 &&
                        !isCompleteLabel(entryLabel)
                      return (
                        <li
                          key={`${key}-${i}-${entryLabel}`}
                          className={`hint-badge${isLast ? '' : ' is-past'}`}
                          data-stage={key}
                        >
                          {isLast && (
                            <span className="hint-dots" aria-hidden="true">
                              <span />
                              <span />
                              <span />
                            </span>
                          )}
                          <span className="hint-text">{entryLabel}</span>
                        </li>
                      )
                    })
                  )}
                </ul>
              </div>
            ))}
          </div>
        )}

        {/* 근거 종합/답변 검증 — fetch(병렬 레인)가 다 끝나야 시작되는
            순차 단계라 레인 아래에 그린다. */}
        {loading && afterLog.length > 0 && (
          <ul className="hint-log">
            {afterLog.map((label, i) => (
              <li
                key={`after-${i}-${label}`}
                className={`hint-badge${i === afterLog.length - 1 ? '' : ' is-past'}`}
                data-stage={progressStageOf(label)}
              >
                {i === afterLog.length - 1 && (
                  <span className="hint-dots" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                )}
                <span className="hint-text">{label}</span>
              </li>
            ))}
          </ul>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  )
}

export default RefineScreen
