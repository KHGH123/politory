"""해석적 판단 문장 / prompt injection 감지 가드레일.

핵심 원칙: 같은 의원의 시간차 발언을 병치할 때 "입장이 바뀌었다" 류의 해석적
판단을 생성하지 않는다. 이 모듈이 응답 생성 후 그 문장을 검사한다.

TODO(C): 금칙 패턴 목록과 판별 로직 설계.
"""


def check(text: str) -> dict:
    """위반 여부를 검사한다. 예: {"interpretive": [...], "injection": [...]}"""
    raise NotImplementedError


def is_violation(text: str) -> bool:
    raise NotImplementedError
