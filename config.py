"""전체 프로젝트가 공유하는 설정. 모든 명령/스크립트는 레포 루트에서 실행한다고 가정한다."""
from functools import lru_cache

from pydantic import Field
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
    # .env의 실제 키 이름(X-NCP-APIGW-API-KEY-ID 등)은 파이썬 식별자로 못 써서
    # validation_alias로 매핑한다.
    NAVER_CLIENT_ID: str = Field(default="", validation_alias="X-NCP-APIGW-API-KEY-ID")
    NAVER_CLIENT_SECRET: str = Field(default="", validation_alias="X-NCP-APIGW-API-KEY")
    WEB_SEARCH_API_KEY: str = ""

    # 저장소
    SQLITE_PATH: str = "./db/uijeonggirok.sqlite3"

    # Vertex AI Search (RAG)
    SEARCH_APP_ID: str = ""

    # 백엔드 (콤마로 구분된 origin 목록)
    CORS_ORIGINS: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
