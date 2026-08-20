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

    # GCP / Vertex AI / Gemini / ADK
    GCP_PROJECT_ID: str = ""
    GCP_REGION: str = "asia-northeast3"
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    GOOGLE_API_KEY: str = ""

    # 웹 검색 도구 (뉴스 2차 출처용)
    WEB_SEARCH_API_KEY: str = ""

    # 저장소
    SQLITE_PATH: str = "./db/uijeonggirok.sqlite3"
    CHROMA_PERSIST_DIR: str = "./db/chroma"
    CHROMA_COLLECTION_NAME: str = "speeches"

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
