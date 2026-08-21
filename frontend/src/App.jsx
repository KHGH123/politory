import { useState } from 'react'
import './App.css'
import LandingScreen from './screens/LandingScreen'
import RefineScreen from './screens/RefineScreen'
import ResultsScreen from './screens/ResultsScreen'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function App() {
  const [stage, setStage] = useState('landing') // 'landing' | 'refine' | 'results'
  const [question, setQuestion] = useState('')
  const [memberName, setMemberName] = useState('')
  const [keywordSuggestions, setKeywordSuggestions] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [result, setResult] = useState(null)

  // 화면1 → 화면2/3 분기: 질문이 충분히 구체적인지는 백엔드(/api/classify)가 판단한다.
  // (LLM 기반 판단 로직은 백엔드 담당 몫 — 여기선 계약만 맞춰 호출)
  async function handleSearchSubmit(e) {
    e.preventDefault()
    if (!question.trim()) {
      setError('질문을 입력해주세요.')
      return
    }
    setError(null)
    setLoading(true)

    try {
      const res = await fetch(`${API_BASE_URL}/api/classify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })

      if (!res.ok) {
        throw new Error(`서버 오류 (${res.status})`)
      }

      const data = await res.json()

      if (data.sufficient) {
        await runQuery(data.member_name || question, '')
      } else {
        setMemberName(data.member_name || question)
        setKeywordSuggestions(data.keywords || [])
        setStage('refine')
        setLoading(false)
      }
    } catch (err) {
      setError(err.message || '요청 중 문제가 발생했습니다.')
      setLoading(false)
    }
  }

  async function runQuery(memberNameValue, keywordValue) {
    setLoading(true)
    setError(null)
    setResult(null)

    try {
      const res = await fetch(`${API_BASE_URL}/api/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question,
          member_name: memberNameValue || null,
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

  function handleReset() {
    setStage('landing')
    setQuestion('')
    setMemberName('')
    setKeywordSuggestions([])
    setResult(null)
    setError(null)
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
        keywordSuggestions={keywordSuggestions}
        onSubmit={(e) => {
          e.preventDefault()
          runQuery(memberName, '')
        }}
        onKeywordClick={(kw) => runQuery(memberName, kw)}
        onReset={handleReset}
        loading={loading}
        error={error}
      />
    )
  }

  return <ResultsScreen question={question} result={result} onReset={handleReset} />
}

export default App
