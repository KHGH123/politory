# 멀티스테이지: frontend(Vite)를 먼저 빌드해 backend/static/에 넣고, FastAPI가
# 그 정적 파일까지 같이 서빙한다(politory 서비스 하나로 프론트+백엔드 통합 —
# backend/main.py의 "프론트엔드 정적 서빙" 섹션 참고).
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ .

# VITE_API_BASE_URL을 비워두면(빈 문자열) 프론트가 상대 경로("")로 fetch해
# 같은 오리진(같은 politory 서비스)으로 API를 호출한다 — frontend/src/App.jsx의
# `import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'` 폴백은 로컬
# 개발(vite dev, 백엔드 별도 포트)용이고, 배포 이미지에서는 같은 오리진이라
# 애초에 base URL이 필요 없다.
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend-build /app/frontend/dist ./backend/static

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
