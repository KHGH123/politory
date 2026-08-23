import { useState } from 'react'
import './App.css'
import LandingScreen from './screens/LandingScreen'
import RefineScreen from './screens/RefineScreen'
import ResultsScreen from './screens/ResultsScreen'

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

      if (data.sufficient) {
        await runQuery(text, data.member_name, null, '')
      } else {
        setMemberName(data.member_name || text)
        setConfirmedMemberName(data.member_name || '')
        setConfirmedParty('')
        setMemberCandidates(data.member_candidates || [])
        setKeywordSuggestions(data.keywords || [])
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
  async function runQuery(effectiveQuestion, memberNameValue, partyValue, keywordValue) {
    setQuestion(effectiveQuestion)
    if (memberNameValue) setMemberName(memberNameValue)
    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const res = await fetch(`${API_BASE_URL}/api/query`, {
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
        throw new Error(`서버 오류 (${res.status})`)
      }

      setResult(await res.json())
      setStage('results')
    } catch (err) {
      setError(err.message || '요청 중 문제가 발생했습니다.')
    } finally {
      setLoading(false)
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
        error={error}
      />
    )
  }

  return (
    <ResultsScreen
      question={question}
      memberName={memberName}
      result={result}
      onReset={handleBackFromResults}
    />
  )
}

export default App
