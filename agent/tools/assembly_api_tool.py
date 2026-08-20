"""열린국회정보 Open API(open.assembly.go.kr) 호출 툴.

scripts/verify_data_source.py로 실제 API_ID/응답 필드를 먼저 확인한 뒤 구현한다.
mcp_server/를 통해 노출할 예정 — docstring(Args/Returns)을 도구 설명으로 잘 채울 것.
"""


def fetch_bill_info(member_name: str) -> dict:
    raise NotImplementedError


def fetch_vote_result(bill_id: str) -> dict:
    raise NotImplementedError


def fetch_member_info(member_name: str) -> dict:
    raise NotImplementedError
