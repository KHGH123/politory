"""Vertex AI Search(AI Applications) 데이터스토어/앱 접근점.

콘솔에서 데이터스토어+앱을 만들고 발언 텍스트(구조화 메타데이터 포함)를
Cloud Storage에 업로드해서 ingest한 뒤, 앱 ID를 .env의 SEARCH_APP_ID로 넣는다.
"""


def get_search_app_id() -> str:
    raise NotImplementedError
