"""열린국회정보 Open API에서 의안/표결/의원 정형 데이터를 1회성으로 수집해 raw JSON으로 저장.

사용법: python -m pipeline.collect_assembly_api
스케줄러/폴링은 스코프 밖. 필요할 때 수동 재실행하는 1회성 스크립트다.
"""
from pathlib import Path

RAW_DIR = Path("data/raw")

# MVP 스코프: 22대, 특정 상임위 1~2개, 의원 3~5명으로 한정
TARGET_ASSEMBLY = 22
TARGET_COMMITTEES = ["국토교통위원회"]
TARGET_MEMBERS: list[str] = []  # TODO: 데모 대상 의원 3~5명 채워 넣기


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    # TODO: agent.tools.assembly_api_tool의 fetch_* 함수로 의안/표결/의원 정보를 수집해
    # data/raw/bills.json, data/raw/votes.json, data/raw/members.json으로 저장
    raise NotImplementedError


if __name__ == "__main__":
    main()
