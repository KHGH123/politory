"""Day 1 검증: sample 키로 열린국회정보 회의록 API를 호출해 응답 구조를 출력한다.

사용법: python scripts/verify_data_source.py --api-id <API_ID>
API_ID는 open.assembly.go.kr > OPEN API 활용안내에서 회의록 관련 API를 찾아 확인.
"""
import argparse
import json

import httpx

BASE_URL = "https://open.assembly.go.kr/portal/openapi/{api_id}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-id", required=True)
    parser.add_argument("--key", default="sample")
    args = parser.parse_args()

    url = BASE_URL.format(api_id=args.api_id)
    resp = httpx.get(url, params={"KEY": args.key, "Type": "json", "pIndex": 1, "pSize": 5}, timeout=15)
    resp.raise_for_status()
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
