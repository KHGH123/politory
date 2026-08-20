"""발언 텍스트 청킹. 파싱 결과(parse_pdf/parse_nanet)를 임베딩 입력 단위로 자른다.

청킹 전략(길이, 오버랩 등)은 D(RAG 설계)와 합의해서 정한다.
"""


def chunk_speech(speech: dict) -> list[dict]:
    raise NotImplementedError
