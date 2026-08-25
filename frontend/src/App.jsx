import { useState } from 'react'
import './App.css'
import LandingScreen from './screens/LandingScreen'
import RefineScreen from './screens/RefineScreen'
import ResultsScreen from './screens/ResultsScreen'
import { parseSseStream } from './utils'

// VITE_API_BASE_URL이 아예 설정 안 됐으면(로컬에서 vite dev로 프론트만 띄운
// 경우) localhost:8000으로 폴백한다. 빈 문자열("")로 명시적으로 설정된
// 경우(배포 이미지 — 프론트와 백엔드가 같은 Cloud Run 서비스라 상대 경로로
// 같은 오리진을 호출해야 함)는 폴백하지 않고 그대로 빈 문자열을 쓴다.
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL !== undefined
    ? import.meta.env.VITE_API_BASE_URL
    : 'http://localhost:8000'

function App() {
  const [stage, setStage] = useState('landing') // 'landing' | 'refine' | 'results'
  const [question, setQuestion] = useState('')
  const [memberName, setMemberName] = useState('')
  // classify가 DB에서 실존을 확인해준 이름만 여기 들어감 (memberName은 화면2에서
  // 자유 편집 가능한 표시값이라 이름이 아닌 정책 텍스트가 섞일 수 있음 — /api/query는
  // member_name이 DB에 없으면 404를 내므로 미확정 텍스트를 member_name으로 보내면 안 됨)
  const [confirmedMemberName, setConfirmedMemberName] = useState('')
  const [confirmedParty, setConfirmedParty] = useState('')
  // 동명이인 후보 목록 (classify가 이름만으로 특정 못 했을 때)
  const [memberCandidates, setMemberCandidates] = useState([])
  const [keywordSuggestions, setKeywordSuggestions] = useState([])
  const [loading, setLoading] = useState(false)
  // runQuery(POST /api/query/stream) 진행 중 지금까지 도착한 진행 문구를
  // 전부 쌓아 로그처럼 보여준다("법안·표결 기록 조회 중..." 하나만 계속
  // 갈아끼우지 말고 아래로 계속 쌓아달라는 피드백 반영). classifyAndRoute의
  // 로딩(loading만 씀)과는 구분되는 값이라 별도 state로 둔다 — 빈 배열이면
  // RefineScreen이 기본 문구 한 줄로 폴백한다.
  const [progressLog, setProgressLog] = useState([])
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  // 질문 텍스트 하나를 /api/classify에 넣어 화면2/3로 분기한다. 화면1의 최초
  // 제출뿐 아니라, 화면2의 "특정인/정책" 입력창에 완전히 새 문장을 타이핑해서
  // 바로 제출하는 경우에도 이 함수를 다시 태워야 한다 — 예전 confirmedMemberName을
  // 그대로 재사용하면(예: "이재명" 확정 후 화면2에서 "주식 정책 알려줘 홍길동"으로
  // 바꿔 쳐도 여전히 이재명으로 조회되거나, 애초에 미확정이면 member_name=null로
  // 나가 화면3 약력 카드가 통째로 비어버리는 버그가 있었다).
  async function classifyAndRoute(text) {
    if (!text.trim()) {
      setError('질문을 입력해주세요.')
      return
    }
    setError(null)
    setLoading(true)

    try {
      const res = await fetch(`${API_BASE_URL}/api/classify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text }),
      })

      if (!res.ok) {
        throw new Error(`서버 오류 (${res.status})`)
      }

      const data = await res.json()

      const candidates = data.member_candidates || []
      const keywords = data.keywords || []

      if (data.sufficient) {
        await runQuery(text, data.member_name, null, '')
      } else if (!data.member_name && candidates.length === 0 && keywords.length === 0) {
        // 등록된 국회의원으로도, 관련 상임위 인물로도 전혀 특정하지 못한 경우
        // (예: 정치인이 아닌 이름) — 화면2로 보내봤자 고를 것도 채울 것도
        // 없는 빈 화면이라 오히려 뭐가 잘못됐는지 알기 어렵다. 화면 전환 없이
        // 지금 있던 화면(화면1 또는 화면2)에 바로 에러만 띄운다.
        setError('등록된 국회의원을 찾지 못했습니다. 이름이나 표현을 다시 확인해주세요.')
        setLoading(false)
      } else {
        // 화면2 입력창엔 추출된 인물명(data.member_name)이 아니라 사용자가 실제로
        // 입력한 원본 질문을 그대로 보여준다 — "이재명 주식 정책에 대해 알려줘"라고
        // 쳤는데 "이재명"만 남아있으면 뭘 검색 중이었는지 잊어버리는 문제가 있었다.
        // 인물 특정 자체는 confirmedMemberName(아래)이 별도로 들고 있어 영향 없음.
        setMemberName(text)
        setConfirmedMemberName(data.member_name || '')
        setConfirmedParty('')
        setMemberCandidates(candidates)
        setKeywordSuggestions(keywords)
        setStage('refine')
        setLoading(false)
      }
    } catch (err) {
      setError(err.message || '요청 중 문제가 발생했습니다.')
      setLoading(false)
    }
  }

  // 화면1 → 화면2/3 분기: 질문이 충분히 구체적인지는 백엔드(/api/classify)가 판단한다.
  // (LLM 기반 판단 로직은 백엔드 담당 몫 — 여기선 계약만 맞춰 호출)
  async function handleSearchSubmit(e) {
    e.preventDefault()
    await classifyAndRoute(question)
  }

  // effectiveQuestion: 화면2에서 "특정인/정책" 칸을 수정했으면 그 값이 실제 검색어가 되어야 함
  // (원래 화면1 질문을 그대로 쓰면 화면2에서 고친 내용이 무시되는 버그가 있었음)
  //
  // /api/query/stream(SSE)을 쓴다 — 표준 EventSource는 GET 전용이라 POST
  // body(question/member_name/...)를 못 보내므로, fetch + ReadableStream을
  // 직접 파싱한다(parseSseStream, utils.js). 스트림이 실패하거나(네트워크 등)
  // 서버가 progress만 보내고 result 없이 끊기면 사용자에게는 항상 명확한
  // 에러를 보여준다 — "응답이 왔는데 결과가 없다"는 침묵 실패를 피한다.
  async function runQuery(effectiveQuestion, memberNameValue, partyValue, keywordValue) {
    setQuestion(effectiveQuestion)
    if (memberNameValue) setMemberName(memberNameValue)
    setLoading(true)
    setProgressLog([])
    setError(null)
    setResult(null)

    try {
      const res = await fetch(`${API_BASE_URL}/api/query/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: effectiveQuestion,
          member_name: memberNameValue || null,
          party: partyValue || null,
          keyword: keywordValue || null,
        }),
      })

      if (!res.ok) {
        // 검증 실패(400/404 등)는 스트림이 시작되기 전에 HTTPException으로
        // 온다 — backend/main.py의 _validate_query_request 참고.
        throw new Error(`서버 오류 (${res.status})`)
      }

      let finalResult = null
      // 마지막 progress 문구가 화면에 뜬 시각. guardrail("답변 검증 중")처럼
      // 단계별 소요시간이 매번 크게 다른 경우(실측: 0.1초~10초) 마지막
      // 문구가 뜨자마자 result가 바로 도착하면 그 배지를 사용자가 인지할
      // 새도 없이 결과 화면으로 넘어가버린다("검증 단계는 거의 1초도 안
      // 나오는 것 같다"는 피드백으로 발견) — 아래에서 result 처리 전 최소
      // 노출 시간을 보정한다.
      let lastLabelShownAt = 0
      for await (const evt of parseSseStream(res.body)) {
        if (evt.event === 'progress') {
          // 재검색 루프(LoopAgent)로 같은 문구가 연달아 다시 올 수 있다
          // (예: "법안·표결 기록 0건 확인"이 계속 반복). 로그 아래로 계속
          // 쌓되, 바로 직전과 완전히 같은 문구면 중복 줄을 새로 추가하지
          // 않고 넘어간다 — 안 그러면 재시도가 몰릴 때 같은 줄이 수십 번
          // 찍혀 로그가 오히려 읽기 어려워진다.
          setProgressLog((prev) => {
            if (prev.length > 0 && prev[prev.length - 1] === evt.data) return prev
            lastLabelShownAt = Date.now()
            return [...prev, evt.data]
          })
        } else if (evt.event === 'result') {
          finalResult = JSON.parse(evt.data)
        } else if (evt.event === 'error') {
          throw new Error(evt.data)
        }
      }

      if (!finalResult) {
        // 파이프라인 내부 예외 없이 스트림만 조용히 끊긴 비정상 케이스
        // (예: 프록시가 SSE 연결을 중간에 자름) — 원인불명 무응답 대신
        // 명확한 에러로 알린다.
        throw new Error('응답을 받지 못했습니다. 다시 시도해주세요.')
      }

      // 마지막 진행 배지가 뜬 지 MIN_LABEL_MS도 안 됐으면 그만큼 더
      // 기다렸다가 결과 화면으로 넘어간다 — 실제 소요시간이 그보다
      // 길었던 단계(대부분의 경우)는 이 시점에 이미 지연이 0이라 영향이
      // 없고, 우연히 아주 짧게 끝난 단계만 최소한으로 보정된다.
      const MIN_LABEL_MS = 600
      const elapsedSinceLastLabel = Date.now() - lastLabelShownAt
      if (elapsedSinceLastLabel < MIN_LABEL_MS) {
        await new Promise((resolve) => setTimeout(resolve, MIN_LABEL_MS - elapsedSinceLastLabel))
      }

      setResult(finalResult)
      setStage('results')
    } catch (err) {
      setError(err.message || '요청 중 문제가 발생했습니다.')
    } finally {
      setLoading(false)
      setProgressLog([])
    }
  }

  // 후보 카드 선택: 이름 + 정당까지 확정해서 이후 검색이 그 사람으로 특정되게 함.
  // keywordSuggestions가 있으면(동명이인 케이스) 다음 키워드 선택을 기다리고,
  // 없으면(정책 질문에서 역으로 추천된 의원 카드 케이스) 더 물을 게 없으니
  // 원래 질문 그대로 바로 조회한다.
  function handleCandidateSelect(candidate) {
    setMemberName(candidate.name)
    setConfirmedMemberName(candidate.name)
    setConfirmedParty(candidate.party || '')
    setMemberCandidates([])

    if (keywordSuggestions.length === 0) {
      runQuery(question, candidate.name, candidate.party || null, question)
    }
  }

  // 화면2 → 화면1: 완전히 처음으로
  function handleBackToLanding() {
    setStage('landing')
    setQuestion('')
    setMemberName('')
    setConfirmedMemberName('')
    setConfirmedParty('')
    setMemberCandidates([])
    setKeywordSuggestions([])
    setResult(null)
    setError(null)
  }

  // 화면3 → 화면2 (화면2를 거쳐왔으면) 또는 화면1 (화면1에서 바로 왔으면)
  function handleBackFromResults() {
    setResult(null)
    setError(null)
    if (keywordSuggestions.length > 0) {
      setStage('refine')
    } else {
      setStage('landing')
      setQuestion('')
      setMemberName('')
      setConfirmedMemberName('')
      setConfirmedParty('')
    }
  }

  if (stage === 'landing') {
    return (
      <LandingScreen
        question={question}
        onQuestionChange={setQuestion}
        onSubmit={handleSearchSubmit}
        loading={loading}
        error={error}
      />
    )
  }

  if (stage === 'refine') {
    return (
      <RefineScreen
        memberName={memberName}
        onMemberNameChange={setMemberName}
        memberCandidates={memberCandidates}
        onCandidateSelect={handleCandidateSelect}
        keywordSuggestions={keywordSuggestions}
        onSubmit={(e) => {
          e.preventDefault()
          // 인물이 이미 확정돼 있고(confirmedMemberName) 입력창이 여전히 그
          // 인물을 가리키는 텍스트면(예: "이재명" -> "이재명 주식") 같은 사람의
          // 주제만 좁히는 것이므로 classify를 다시 태우지 않고 바로 조회한다 —
          // classify의 LLM은 주제가 아무리 좁혀져도("주식" -> "이재명 주식")
          // 법안/제도명 수준이 아니면 계속 "안 구체적"이라고 판단해 화면2에서
          // 못 벗어나는 무한루프가 생길 수 있다(실측 확인). 입력창 텍스트를
          // 완전히 다른 사람으로 바꾼 경우(confirmedMemberName이 더 이상 포함
          // 안 됨)에만 classifyAndRoute로 처음부터 다시 판단한다.
          if (confirmedMemberName && memberName.includes(confirmedMemberName)) {
            runQuery(memberName, confirmedMemberName, confirmedParty || null, memberName)
          } else {
            classifyAndRoute(memberName)
          }
        }}
        onKeywordClick={(kw) =>
          // effectiveQuestion에 memberName만 넘기면 화면3의 "Q. {question}"이
          // 사용자가 화면1에 실제로 입력한 원본 질문이 아니라 인물명만 뜨는
          // 버그가 있었다. 원본은 question state에 그대로 남아있으므로(화면2
          // 진입 시 memberName/confirmedMemberName 등만 갱신되고 question은
          // 안 건드림) 그걸 그대로 쓴다 — handleCandidateSelect와 동일한 패턴.
          runQuery(question, confirmedMemberName || null, confirmedParty || null, kw)
        }
        onReset={handleBackToLanding}
        loading={loading}
        progressLog={progressLog}
        error={error}
      />
    )
  }

  return (
    <ResultsScreen
      question={question}
      result={result}
      onReset={handleBackFromResults}
    />
  )
}

export default App
