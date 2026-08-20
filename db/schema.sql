-- 정형 데이터 스키마 초안. 실제 컬럼은 A가 열린국회정보 API 응답 구조 확인 후 확정.

CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bills (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS votes (
    id TEXT PRIMARY KEY,
    bill_id TEXT REFERENCES bills(id),
    member_id TEXT REFERENCES members(id)
);
