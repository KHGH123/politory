"""FastAPI 계약 테스트.

Gemini, BigQuery, MCP/ADK를 실제 호출하지 않고 HTTP 상태 코드와 응답 스키마,
분기 로직만 검증한다. 외부 클라이언트 생성자는 backend.main import 전에
mock해서 ADC가 없는 CI 환경에서도 테스트를 수집할 수 있게 한다.
"""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def backend_module():
    with (
        patch("google.cloud.bigquery.Client"),
        patch("google.genai.Client"),
    ):
        yield importlib.import_module("backend.main")


@pytest.fixture(scope="module")
def client(backend_module):
    with TestClient(backend_module.app) as test_client:
        yield test_client


def _set_classify_llm_result(backend_module, **overrides):
    payload = {
        "sufficient": True,
        "member_name": None,
        "keywords": [],
        "district_guess": None,
        "committee_guess": None,
        **overrides,
    }
    backend_module._genai_client.models.generate_content.return_value = SimpleNamespace(
        text=json.dumps(payload, ensure_ascii=False)
    )


def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/api/classify", "/api/query"])
def test_empty_question_is_rejected(client, path):
    response = client.post(path, json={"question": "   "})

    assert response.status_code == 400
    assert response.json() == {"detail": "질문을 입력해주세요."}


def test_classify_returns_homonym_candidates(client, backend_module, monkeypatch):
    _set_classify_llm_result(
        backend_module,
        sufficient=True,
        member_name="박지원",
    )
    monkeypatch.setattr(
        backend_module,
        "_find_members_by_name",
        lambda name: [
            backend_module.MemberCandidate(
                name="박지원",
                legislator_id="krna:H7X3372O",
                party="더불어민주당",
                district="전북 군산시김제시부안군을",
                image_url="https://example.test/park-1.jpg",
            ),
            backend_module.MemberCandidate(
                name="박지원",
                legislator_id="krna:8BF5855P",
                party="더불어민주당",
                district="전남 해남군완도군진도군",
                image_url="https://example.test/park-2.jpg",
            ),
        ],
    )

    response = client.post(
        "/api/classify",
        json={"question": "박지원 의원의 지역 현안 발언을 알려줘"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sufficient"] is False
    assert body["member_name"] is None
    assert body["keywords"] == []
    assert len(body["member_candidates"]) == 2
    assert {candidate["name"] for candidate in body["member_candidates"]} == {"박지원"}
    assert {candidate["legislator_id"] for candidate in body["member_candidates"]} == {
        "krna:H7X3372O",
        "krna:8BF5855P",
    }


def test_classify_returns_district_candidates(client, backend_module, monkeypatch):
    _set_classify_llm_result(
        backend_module,
        sufficient=False,
        district_guess="분당갑",
    )
    monkeypatch.setattr(
        backend_module,
        "_find_members_by_district",
        lambda district: [
            backend_module.MemberCandidate(
                name="안철수",
                legislator_id="krna:district-test",
                party="국민의힘",
                district="경기 성남시분당구갑",
            )
        ],
    )

    response = client.post(
        "/api/classify",
        json={"question": "분당갑 지역구 의원을 찾아줘"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sufficient"] is False
    assert body["member_name"] is None
    assert body["keywords"] == []
    assert body["member_candidates"] == [
        {
            "name": "안철수",
            "legislator_id": "krna:district-test",
            "party": "국민의힘",
            "district": "경기 성남시분당구갑",
            "image_url": None,
        }
    ]


def test_classify_clears_suggestions_for_unknown_member(
    client, backend_module, monkeypatch
):
    _set_classify_llm_result(
        backend_module,
        sufficient=False,
        member_name="홍길동",
        keywords=[{"title": "주거 정책", "reason": "관련 정책"}],
        committee_guess="국토교통위원회",
    )
    monkeypatch.setattr(backend_module, "_find_members_by_name", lambda name: [])

    response = client.post(
        "/api/classify",
        json={"question": "홍길동 의원의 주거 정책을 알려줘"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sufficient"] is False
    assert body["member_name"] is None
    assert body["keywords"] == []
    assert body["member_candidates"] == []


def test_query_rejects_unknown_member(client, backend_module, monkeypatch):
    run_agent = AsyncMock()
    monkeypatch.setattr(
        backend_module,
        "_get_member_profile",
        lambda name, party=None, legislator_id=None: None,
    )
    monkeypatch.setattr(backend_module, "_run_agent", run_agent)

    response = client.post(
        "/api/query",
        json={
            "question": "홍길동 의원의 주거 정책을 알려줘",
            "member_name": "홍길동",
            "party": None,
            "keyword": "주거 정책",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "등록된 국회의원이 아닙니다."}
    run_agent.assert_not_awaited()


def test_query_returns_profile_answer_and_source(
    client, backend_module, monkeypatch
):
    profile = backend_module.MemberProfile(
        name="박지원",
        legislator_id="krna:H7X3372O",
        party="더불어민주당",
        district="전북 군산시김제시부안군을",
        term_count=1,
    )
    agent_response = backend_module.AgentResponse(
        answer="새만금 사업 관련 발언입니다[1].",
        sources=[
            backend_module.AgentSource(
                type="primary",
                title="국회 본회의 회의록",
                legislator_id="krna:H7X3372O",
                excerpt="새만금 사업 관련 발언 원문",
                description="새만금 사업의 지속적인 추진을 촉구했다.",
                url="https://example.test/assembly.pdf",
                date="2026-06-05",
                page_start=10,
                page_end=10,
            )
        ],
    )
    run_agent = AsyncMock(return_value=agent_response)
    monkeypatch.setattr(
        backend_module,
        "_get_member_profile",
        lambda name, party=None, legislator_id=None: profile,
    )
    monkeypatch.setattr(backend_module, "_run_agent", run_agent)

    response = client.post(
        "/api/query",
        json={
            "question": "전북 박지원 의원의 새만금 관련 발언을 알려줘",
            "member_name": "박지원",
            "legislator_id": "krna:H7X3372O",
            "party": "더불어민주당",
            "keyword": "새만금",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "새만금 사업 관련 발언입니다[1]."
    assert body["member_profile"]["district"] == "전북 군산시김제시부안군을"
    assert len(body["sources"]) == 1
    assert body["sources"][0]["legislator_id"] == "krna:H7X3372O"
    assert body["sources"][0]["url"] == "https://example.test/assembly.pdf"
    run_agent.assert_awaited_once_with(
        "전북 박지원 의원의 새만금 관련 발언을 알려줘",
        "박지원",
        "새만금",
        "krna:H7X3372O",
    )


def test_eight_footnotes_match_date_sorted_sources(backend_module):
    """8개 출처를 정렬한 뒤 각주 번호가 최종 배열 인덱스와 일치한다."""
    response = backend_module.AgentResponse(
        answer=" ".join(f"근거{s}⟦s{s}⟧" for s in range(1, 9)),
        sources=[
            backend_module.AgentSource(
                ref_id=f"s{s}",
                type="primary",
                title=f"출처{s}",
                url=f"https://example.test/source-{s}",
                date=f"2026-01-{9 - s:02d}",
            )
            for s in range(1, 9)
        ],
    )

    response.sources = sorted(response.sources, key=backend_module._source_sort_key)
    backend_module._resolve_footnote_numbers(response)

    assert [source.title for source in response.sources] == [
        "출처8",
        "출처7",
        "출처6",
        "출처5",
        "출처4",
        "출처3",
        "출처2",
        "출처1",
    ]
    assert response.answer == (
        "근거1[8] 근거2[7] 근거3[6] 근거4[5] "
        "근거5[4] 근거6[3] 근거7[2] 근거8[1]"
    )
    assert all(source.ref_id == "" for source in response.sources)


def test_query_allows_question_without_member(client, backend_module, monkeypatch):
    run_agent = AsyncMock(
        return_value=backend_module.AgentResponse(
            answer="수집된 정보가 없습니다.",
            sources=[],
        )
    )
    monkeypatch.setattr(backend_module, "_run_agent", run_agent)

    response = client.post(
        "/api/query",
        json={
            "question": "최근 국회 교통 정책을 알려줘",
            "member_name": None,
            "party": None,
            "keyword": "교통 정책",
        },
    )

    assert response.status_code == 200
    assert response.json()["member_profile"] is None
    assert response.json()["sources"] == []
    run_agent.assert_awaited_once()
