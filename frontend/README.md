# frontend

React (Vite). 의원 검색/타임라인 조회 화면.

## 실행

```
npm install
npm run dev
```

기본으로 `http://localhost:5173`에서 뜬다. 백엔드(`uvicorn backend.main:app --reload`, 기본 `http://localhost:8000`)도 같이 띄워야 `/api/query` 호출이 됨.

## 구조

- `src/App.jsx` — 검색창 + 의원명/키워드 입력 + 결과(답변, 출처) 화면
- `.env` — `VITE_API_BASE_URL` (백엔드 주소)
