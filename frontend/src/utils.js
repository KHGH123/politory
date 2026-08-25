export function truncate(str, max = 40) {
  if (!str) return ''
  return str.length > max ? `${str.slice(0, max)}…` : str
}

// backend/main.py의 _START_LABELS/완료 문구(예: "법안·표결 기록 조회 중...",
// "회의록 발언 5건 조회")를 진행 배지의 색상 카테고리로 분류한다.
// RefineScreen.jsx가 이 값을 <span data-stage={...}>에 넣어 App.css의
// .hint-badge[data-stage='...'] 색을 고른다 — "글자 색이 안 바뀌어 밋밋하다"는
// 피드백으로, 단계별로 다른 색조를 주기 위함. 백엔드 문구 자체를 파싱하므로
// backend/main.py의 _START_LABELS 문구를 바꾸면 이 매핑도 같이 확인해야 한다.
const _PROGRESS_STAGE_PATTERNS = [
  [/법안|표결/, 'action'],
  [/회의록|발언/, 'speech'],
  [/뉴스/, 'context'],
  [/근거 종합/, 'synthesis'],
  [/답변 검증/, 'verify'],
]

export function progressStageOf(label) {
  if (!label) return null
  const match = _PROGRESS_STAGE_PATTERNS.find(([pattern]) => pattern.test(label))
  return match ? match[1] : null
}

// 착수 문구(_START_LABELS)와 완료 문구(건수/조회 완료) 구분. root_agent
// 파이프라인 자체(agent/agent.py: query_processing -> fetch -> evidence_synthesis)를
// 그대로 반영한 5단계 트래커를 그리기 위한 것 — "에이전트 구조가 좀 더
// 시각화됐으면"이라는 피드백으로 추가. fetch 단계(action/speech/context)는
// ParallelAgent라 실제로 동시에 진행되므로, 트래커에서도 한 그룹으로 묶어
// "동시에 돈다"는 걸 보여준다.
// "검증 보류(근거 불충분)"(backend/main.py의 _VERIFIER_EXHAUSTED_LABELS)도
// 완료 취급한다 — speech/action이 재검색을 다 쓰고도 끝내 검증 통과를
// 못한 채 다음 단계(merge)로 넘어간 경우인데, 이걸 미완료로 두면 직전
// "검증 중" 착수 줄이 안 지워지고 겹쳐 남거나(splitProgressLog) 트래커가
// 그 stage를 계속 진행 중으로 보여준다(computeStepStatuses) — 실제로는
// (성공은 아니지만) 그 stage의 검증 절차 자체는 끝난 상태다.
const _COMPLETE_PATTERNS = [/\d+건 조회/, /조회 완료/, /분석 완료/, /검증 완료/, /검증 보류/]

// RefineScreen.jsx도 쓴다 — beforeLog(질문 분석)에 "질문 분석 완료"가
// 도착하면 그 줄은 착수 문구가 아니라 완료 문구이므로, fetch가 아직 안
// 시작됐더라도 무조건 펄스 없이(is-past) 보여줘야 한다("완료"라고 써놓고
// 옆에 진행 중 점이 깜빡이면 모순돼 보인다).
export function isCompleteLabel(label) {
  return _COMPLETE_PATTERNS.some((p) => p.test(label))
}

// 트래커에 그릴 5단계. parallel:true인 그룹은 stages 배열 안 여러 stage 키를
// 한 칸에 같이 그린다(App.jsx/RefineScreen.jsx가 이 순서 그대로 렌더).
export const PROGRESS_STEPS = [
  { key: 'query_processing', label: '질문 분석', stages: [null] },
  {
    key: 'fetch',
    label: '자료 조회',
    stages: ['action', 'speech', 'context'],
    stageLabels: { action: '법안·표결', speech: '회의록', context: '뉴스' },
    parallel: true,
  },
  { key: 'synthesis', label: '근거 종합', stages: ['synthesis'] },
  { key: 'verify', label: '답변 검증', stages: ['verify'] },
]

// progressLog(App.jsx가 SSE progress 이벤트를 쌓은 배열) 하나로부터 각
// PROGRESS_STEPS 항목의 상태를 계산한다. 반환값은
// { [stepKey]: 'pending' | 'active' | 'done' } — fetch처럼 stage가 여러 개인
// 그룹은 하위 stage별 상태를 substatus에 따로 담아 부분 진행(예: 3개 중 1개
// 완료)도 표현할 수 있게 한다.
//
// 판정 규칙: 어떤 stage의 "완료" 문구(건수/조회완료)가 로그에 한 번이라도
// 있으면 done. 없지만 "착수" 문구가 있으면 active. 둘 다 없으면 pending.
// query_processing은 stage 매핑이 없는(null) 특수 케이스라, 로그에 첫 항목이
// 있으면(질문 분석 착수 문구 자체가 로그의 시작) done으로 취급 — 이후
// 단계(fetch 등)가 하나라도 로그에 잡히면 이 앞 단계는 이미 끝난 것이기
// 때문이다.
export function computeStepStatuses(progressLog) {
  const seenStage = new Map() // stage key -> 'active' | 'done'
  for (const label of progressLog) {
    const s = progressStageOf(label)
    if (!s) continue
    const nextStatus = isCompleteLabel(label) ? 'done' : 'active'
    // 재검색 루프로 같은 stage가 여러 번 오갈 수 있다(active -> done ->
    // active -> done ...). 마지막으로 관찰된 상태를 신뢰한다.
    seenStage.set(s, nextStatus)
  }
  // merge("근거 종합 중")는 완료(건수) 문구가 따로 없는 단계다 — evidence
  // 개수 같은 정량 결과가 없어서다(backend/main.py의 merge/guardrail
  // 매핑 참고). guardrail("답변 검증 중")이 착수했다는 건 merge가 이미
  // 끝났다는 뜻이므로, 이 순서 관계로 synthesis를 done으로 보정한다 —
  // 안 그러면 verify가 active인 동안 synthesis도 계속 active로 남아
  // "둘이 동시에 진행 중"처럼 잘못 보인다.
  if (seenStage.has('verify') && seenStage.get('synthesis') === 'active') {
    seenStage.set('synthesis', 'done')
  }

  const anyStageSeen = seenStage.size > 0
  const statuses = {}

  for (const step of PROGRESS_STEPS) {
    if (step.key === 'query_processing') {
      // fetch 단계(다음 단계)가 이미 시작됐다는 건 query_processing이 끝났다는
      // 뜻. 아직 아무 stage도 안 잡혔지만 로그 자체는 있다면(예: "요청 준비
      // 중" 자리표시가 아니라 실제 첫 이벤트) query_processing이 진행 중.
      statuses[step.key] = anyStageSeen ? 'done' : progressLog.length > 0 ? 'active' : 'pending'
      continue
    }
    if (step.parallel) {
      const subStatuses = step.stages.map((s) => seenStage.get(s) || 'pending')
      statuses[step.key] = {
        overall: subStatuses.every((s) => s === 'done')
          ? 'done'
          : subStatuses.some((s) => s !== 'pending')
            ? 'active'
            : 'pending',
        sub: Object.fromEntries(step.stages.map((s, i) => [s, subStatuses[i]])),
      }
      continue
    }
    statuses[step.key] = seenStage.get(step.stages[0]) || 'pending'
  }

  return statuses
}

// fetch 단계(법안·표결/회의록/뉴스)는 실제로 ParallelAgent라 동시에 진행되는데,
// progressLog를 그냥 순서대로 한 줄씩 쌓으면 도착 순서(=완료 순서)로만
// 보여서 "이 셋이 동시에 진행 중"이라는 사실이 로그에서 드러나지 않는다
// ("병렬 처리니까 그거에 맞게 로그"라는 피드백). splitProgressLog가 로그
// 하나를 4갈래로 쪼갠다:
//   before — query_processing(질문 분석). fetch(병렬 레인)보다 항상 먼저
//     오는 유일한 순차 단계라 레인 위에 그린다.
//   lanes.action / lanes.speech / lanes.context — fetch의 3갈래를 각각
//     독립된 레인(컬럼)에 쌓는다. 화면에서 세 레인을 나란히 배치하면
//     "동시에 돈다"는 게 레이아웃 자체로 드러난다.
//   after — synthesis(근거 종합)/verify(답변 검증). fetch가 다 끝나야
//     시작되는 단계라 레인 아래에 그린다("병합 단계가 아래에 나오도록"이라는
//     피드백 — before/after를 합쳐 하나의 sequential로 뒀을 때는 병합/검증이
//     항상 레인보다 위쪽 블록에 섞여 있어 실제 실행 순서(분석 -> 병렬 조회
//     -> 종합 -> 검증)와 화면 순서가 어긋났다).
export function splitProgressLog(progressLog) {
  const before = []
  const after = []
  const lanes = { action: [], speech: [], context: [] }
  for (const label of progressLog) {
    const stage = progressStageOf(label)
    if (stage === 'action' || stage === 'speech' || stage === 'context') {
      // "조회 중"->"N건 조회", "검증 중"->"OO 검증 완료"가 각자 자기만의
      // 착수/완료 쌍을 갖는다(동사를 조회/검증으로 분리한 뒤로). 완료
      // 문구가 도착하면, 바로 직전 줄이 그 완료와 짝을 이루는 착수
      // 문구("~조회 중" 또는 "~검증 중")일 때만 그 착수 줄을 지우고
      // 완료 문구로 교체한다 — beforeLog(질문 분석)에 이미 적용한
      // "완료됐으면 '중'은 지우자"는 규칙을 레인에도 그대로 적용한
      // 것("조회 중 같은거 조회 완료되면 지우자"는 피드백). "검증 중"
      // 다음에 "검증 완료"가 와도 같은 규칙으로 "검증 중"만 지워지고
      // "검증 완료"가 남는다 — 재검색으로 다시 "조회 중"이 오는 경우는
      // 착수 문구라 이 조건에 안 걸려 그대로 새 줄로 남는다.
      const lane = lanes[stage]
      // 착수 문구는 "~조회 중" 그대로거나, action/speech처럼 차수가 붙어
      // "~조회 중 (1차)"로 끝날 수 있다 — endsWith('중')만 쓰면 "(1차)"가
      // 붙은 문구를 놓쳐서 pop이 전혀 안 되는 버그가 있었다("바뀐 거
      // 맞아?"라는 피드백으로 실측 확인: 법안·표결/회의록 둘 다 (N차)가
      // 붙는 stage라 하필 이 버그의 영향을 바로 받았다). "중" 뒤에
      // " (N차)"가 있어도 되도록 정규식으로 완화한다.
      const isPendingStartLabel = (l) => /중(\s*\(\d+차\))?$/.test(l)
      if (isCompleteLabel(label) && lane.length > 0 && isPendingStartLabel(lane[lane.length - 1])) {
        lane.pop()
      }
      if (lane.length > 0 && lane[lane.length - 1] === label) continue
      lane.push(label)
    } else if (stage === 'synthesis' || stage === 'verify') {
      after.push(label)
    } else {
      // stage가 null인 유일한 경우 — query_processing("질문 분석 중" ->
      // "질문 분석 완료"). 완료 문구가 도착하면 직전에 쌓아둔 착수 문구는
      // 이제 볼 일이 없으니 지우고 완료 문구로 교체한다 — "완료됐으면
      // '중'은 로그에서 지워도 되지 않을까"라는 피드백. 두 줄이 겹쳐
      // 쌓이는 대신 항상 최신 상태 한 줄만 보인다.
      if (isCompleteLabel(label) && before.length > 0) {
        before.length = 0
      }
      before.push(label)
    }
  }
  return { before, after, lanes }
}

// backend/main.py의 EventSourceResponse(sse-starlette)가 내보내는 스트림을
// 파싱한다. 표준 EventSource(브라우저 내장)는 GET 전용이라 POST body를 못
// 보내므로 쓸 수 없다 — fetch로 받은 ReadableStream(response.body)을 직접
// SSE 포맷("event: x\ndata: y\n\n", 빈 줄로 이벤트 구분)에 맞춰 자른다.
//
// sse-starlette는 연결 유지를 위해 15초마다 ": ping - <timestamp>"처럼
// ":"로 시작하는 주석 줄도 보낸다 — SSE 스펙상 주석은 이벤트 필드가 아니므로
// 무시해야 한다(그대로 파싱하면 빈 이벤트가 끼어든다).
//
// async generator라 `for await (const evt of parseSseStream(body))`로 소비.
// 각 evt는 { event: "progress"|"result"|"error", data: string }.
// 이벤트 구분자(빈 줄) 정규식. sse-starlette가 실제로 줄바꿈을 "\r\n"(CRLF)으로
// 내보내는 걸 실측으로 확인했다 — buffer.indexOf('\n\n')로만 찾으면 매번
// "\r\n\r\n"이라 경계를 영원히 못 찾고, buffer가 끝없이 쌓이다가 스트림이
// 끝나 이벤트를 단 하나도 못 내보내는 버그가 있었다(Node로 실제 fetch를
// 떠서 재현·확정, curl로는 안 드러남 — curl 출력을 파일로 저장해 grep/cat으로
// 봤을 때 \r이 화면에 안 보여 눈치채지 못했다). "\n"만 오는 서버와도 호환되게
// "\r?\n\r?\n"으로 둘 다 받는다.
const _SSE_EVENT_BOUNDARY = /\r?\n\r?\n/

export async function* parseSseStream(body) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // 이벤트는 빈 줄로 구분된다. 마지막 조각은 다음 청크와 이어질 수
      // 있으니 buffer에 남겨둔다.
      let match
      while ((match = buffer.match(_SSE_EVENT_BOUNDARY))) {
        const rawEvent = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)

        let eventType = 'message'
        const dataLines = []
        // 줄 자체도 "\r\n" 또는 "\n"으로 끝날 수 있어 "\r?\n"으로 나눈다.
        for (const line of rawEvent.split(/\r?\n/)) {
          if (line.startsWith(':')) continue // ping 등 주석 줄, 무시
          if (line.startsWith('event:')) {
            eventType = line.slice('event:'.length).trim()
          } else if (line.startsWith('data:')) {
            dataLines.push(line.slice('data:'.length).trim())
          }
        }
        if (dataLines.length > 0) {
          yield { event: eventType, data: dataLines.join('\n') }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
