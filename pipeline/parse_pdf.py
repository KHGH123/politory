"""회의록 PDF 파싱 (Day 1 검증에서 PDF 경로가 채택될 경우 사용).

국회도서관 발언빅데이터(parse_nanet.py)로 텍스트를 바로 받을 수 있으면
이 모듈은 스킵 가능 — Day 1 go/no-go 결정에 따른다 (docs/day1_checklist.md 참고).
"""
from pathlib import Path


def extract_text(pdf_path: Path) -> str:
    # TODO: pdfplumber 또는 pymupdf(fitz)로 텍스트 추출. 표/단 구조 깨짐 여부 확인 필요.
    raise NotImplementedError


def extract_speeches(pdf_path: Path) -> list[dict]:
    """회의록 원문에서 화자 단위 발언을 분리한다.

    반환 형식: [{"speaker": str, "content": str, "meeting_date": str, "committee": str}]
    """
    # TODO: 화자 구분 패턴(예: "○○○ 위원") 기반으로 발언 단위 분리
    raise NotImplementedError
