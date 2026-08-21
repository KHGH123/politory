"""전체 프로젝트가 공유하는 설정. 모든 명령/스크립트는 레포 루트에서 실행한다고 가정한다."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 열린국회정보 Open API
    ASSEMBLY_API_KEY: str = "sample"

    # 국회도서관 발언빅데이터
    NANET_API_KEY: str = ""

    # 공공데이터포털 국회사무처_회의록 정보 API
    DATA_GO_KR_API_KEY: str = ""

    # GCP / Vertex AI / Gemini / ADK (변수명은 ADK가 그대로 읽는 이름과 맞춤)
    GOOGLE_GENAI_USE_VERTEXAI: bool = True
    GOOGLE_CLOUD_PROJECT: str = ""
    GOOGLE_CLOUD_LOCATION: str = "global"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    MODEL: str = "gemini-3.5-flash"

    # 웹 검색 도구 (뉴스 2차 출처용) — NAVER API HUB 뉴스 검색.
    # 네이버 공식 문서의 실제 HTTP 헤더명은 X-NCP-APIGW-API-KEY-ID /
    # X-NCP-APIGW-API-KEY라 원래 validation_alias로 그 이름을 그대로 매핑해
    # 썼는데, Cloud Run이 하이픈(-)을 포함한 이름의 컨테이너 env를 프로세스에
    # 주입하지 않는다는 걸 실측으로 확인했다(같은 Secret Manager 값을 하이픈
    # 없는 이름으로 참조하면 정상 로딩, 하이픈 있는 이름으로는 os.environ에서
    # 조회 자체가 안 됨 — /debug/env-check로 배포 환경에서 직접 대조). 그
    # 결과 배포 환경에서 이 두 값이 항상 빈 문자열이 되어 search_news가
    # 매번 빈 리스트를 반환, "정보 없음" 응답이 반복되는 게 실제 배포
    # 장애였다. 하이픈 없는 이름으로 통일해 해결 — web_search_tool.py의
    # headers 딕셔너리에서 여전히 실제 HTTP 헤더명(하이픈 포함)으로 보낸다.
    NAVER_CLIENT_ID: str = ""
    NAVER_CLIENT_SECRET: str = ""
    WEB_SEARCH_API_KEY: str = ""

    # 저장소
    SQLITE_PATH: str = "./db/uijeonggirok.sqlite3"

    # BigQuery (국회의원 정형 데이터 — 다른 팀원이 적재)
    # assembly 데이터셋이 실제로 있는 GCP 프로젝트가 GOOGLE_CLOUD_PROJECT(Vertex AI/Gemini용,
    # proj-aj11-...)와 다르다(proj-aj04-...) — 팀원마다 GCP 프로젝트가 갈린 상태.
    # BIGQUERY_PROJECT를 비워두면 GOOGLE_CLOUD_PROJECT로 폴백하고, 채워두면 BigQuery
    # 쿼리에서만 이 값을 쓴다(Vertex AI/Gemini 쪽 프로젝트는 그대로 유지).
    BIGQUERY_PROJECT: str = ""
    BIGQUERY_DATASET: str = ""
    BIGQUERY_MEMBERS_TABLE: str = "MP"

    # Vertex AI Search (RAG)
    SEARCH_APP_ID: str = ""

    # 백엔드 (콤마로 구분된 origin 목록)
    CORS_ORIGINS: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def bigquery_project(self) -> str:
        return self.BIGQUERY_PROJECT or self.GOOGLE_CLOUD_PROJECT


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
