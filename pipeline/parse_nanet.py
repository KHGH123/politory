"""국회도서관 발언빅데이터(dataset.nanet.go.kr) 다운로드 포맷 파싱.

Day 1 최우선 검증 대상: 가입 후 실제 다운로드해서 포맷(CSV/JSON/XML 등)을 확인하고
아래 파싱 로직을 그 포맷에 맞게 채운다. 이게 되면 parse_pdf.py 단계를 생략할 수 있다.
"""
from pathlib import Path


def parse_file(path: Path) -> list[dict]:
    """발언 단위로 파싱된 원본 파일을 표준 스키마로 변환한다.

    반환 형식: [{"speaker": str, "content": str, "meeting_date": str,
                 "committee": str, "source_url": str | None}]
    """
    # TODO: 실제 다운로드 포맷 확인 후 구현
    raise NotImplementedError
