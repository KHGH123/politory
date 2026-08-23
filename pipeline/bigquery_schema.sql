-- BigQuery core schema for National Assembly minutes.
-- Official PDF bytes remain in GCS. BigQuery stores metadata and extracted text.

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.meetings`
(
  meeting_id STRING NOT NULL,
  confer_num INT64,
  assembly_no INT64,
  meeting_type STRING NOT NULL,
  committee_name STRING,
  meeting_date DATE NOT NULL,
  title STRING,
  session_no INT64,
  meeting_no INT64,
  official_url STRING,
  pdf_url STRING,
  raw_gcs_uri STRING,
  raw_html_gcs_uri STRING OPTIONS(description = "폐기된 HTML 수집 경로; 운영 데이터는 NULL"),
  raw_pdf_gcs_uri STRING,
  collected_at TIMESTAMP NOT NULL
)
PARTITION BY meeting_date
CLUSTER BY meeting_type, committee_name, confer_num
OPTIONS(description = "본회의·위원회 등 회의 단위 메타데이터");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.ingestion_documents`
(
  document_id STRING NOT NULL,
  source_type STRING NOT NULL,
  assembly_no INT64,
  meeting_type STRING,
  confer_num INT64,
  api_endpoint STRING,
  source_url STRING,
  pdf_url STRING,
  raw_gcs_uri STRING,
  raw_sha256 STRING,
  source_format STRING,
  parser_version STRING,
  fetch_status STRING,
  parse_status STRING,
  error_message STRING,
  discovered_at TIMESTAMP,
  fetched_at TIMESTAMP,
  parsed_at TIMESTAMP,
  block_count INT64,
  source_text_char_count INT64,
  block_text_char_count INT64,
  validation_status STRING
)
PARTITION BY DATE(discovered_at)
CLUSTER BY source_type, meeting_type, fetch_status, parse_status
OPTIONS(description = "공식 원문 파일의 수집·파싱 이력");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.agendas`
(
  agenda_id STRING NOT NULL,
  meeting_id STRING NOT NULL,
  agenda_no INT64,
  title STRING,
  bill_number STRING,
  bill_id STRING,
  source_anchor STRING,
  collected_at TIMESTAMP NOT NULL
)
CLUSTER BY meeting_id, bill_number
OPTIONS(description = "회의별 안건 및 의안 연결 정보");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.pdf_pages`
(
  page_id STRING NOT NULL,
  meeting_id STRING NOT NULL,
  page_number INT64 NOT NULL,
  extracted_text STRING NOT NULL,
  source_pdf_gcs_uri STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  extraction_method STRING NOT NULL,
  parser_version STRING NOT NULL,
  meeting_date DATE NOT NULL,
  meeting_type STRING NOT NULL,
  committee_name STRING,
  collected_at TIMESTAMP NOT NULL
)
PARTITION BY meeting_date
CLUSTER BY meeting_type, meeting_id
OPTIONS(description = "공식 PDF에서 페이지별로 추출한 원문 텍스트와 근거 위치");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.utterances`
(
  utterance_id STRING NOT NULL,
  meeting_id STRING NOT NULL,
  sequence_no INT64 NOT NULL,
  speaker_member_id STRING,
  source_speaker_id STRING,
  legislator_id STRING,
  speaker_label STRING,
  speaker_name STRING,
  speaker_position STRING,
  utterance_text STRING NOT NULL,
  content_sha256 STRING NOT NULL,
  page_start INT64 NOT NULL,
  page_end INT64 NOT NULL,
  source_pdf_gcs_uri STRING NOT NULL,
  agenda_ids ARRAY<STRING>,
  source_anchor STRING,
  meeting_date DATE NOT NULL,
  meeting_type STRING NOT NULL,
  committee_name STRING,
  collected_at TIMESTAMP NOT NULL,
  parser_version STRING NOT NULL,
  block_id STRING,
  agenda_link_method STRING
)
PARTITION BY meeting_date
CLUSTER BY speaker_member_id, speaker_name, meeting_type, meeting_id
OPTIONS(description = "회의 발언 순서와 근거 위치를 보존한 검색·그라운딩용 발언 데이터");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.legislators`
(
  legislator_id STRING NOT NULL,
  assembly_no INT64 NOT NULL,
  official_member_code STRING,
  name STRING NOT NULL,
  party_name STRING,
  district STRING,
  term_start DATE,
  term_end DATE,
  source STRING,
  source_updated_at TIMESTAMP,
  collected_at TIMESTAMP NOT NULL
)
CLUSTER BY assembly_no, name, official_member_code
OPTIONS(description = "회의록 발언자와 연결할 국회의원 정규화 마스터");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.legislator_terms`
(
  term_id STRING NOT NULL,
  legislator_id STRING NOT NULL,
  assembly_no INT64 NOT NULL,
  name STRING NOT NULL,
  party_name STRING,
  district STRING,
  term_start DATE,
  term_end DATE,
  source STRING,
  collected_at TIMESTAMP
)
CLUSTER BY assembly_no, legislator_id
OPTIONS(description = "정규화 의원별 국회 대수·정당·지역구 이력");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.speaker_identity_map`
(
  speaker_identity_id STRING NOT NULL,
  assembly_no INT64 NOT NULL,
  meeting_id STRING NOT NULL,
  source_speaker_id STRING,
  speaker_name STRING,
  speaker_position STRING,
  legislator_id STRING,
  resolution_status STRING NOT NULL,
  resolution_method STRING,
  confidence FLOAT64,
  resolved_at TIMESTAMP,
  reviewed_at TIMESTAMP,
  collected_at TIMESTAMP NOT NULL
)
CLUSTER BY assembly_no, source_speaker_id, speaker_name, legislator_id
OPTIONS(description = "회의록 출처별 발언자 ID를 정규화 의원 ID에 연결하는 매핑");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.search_documents`
(
  id STRING NOT NULL,
  jsonData STRING
)
OPTIONS(description = "Vertex AI Search structured-data import documents (id, jsonData)");

CREATE TABLE IF NOT EXISTS `proj-aj04-211200020328.assembly.vote_search_documents`
(
  id STRING NOT NULL,
  jsonData STRING
)
OPTIONS(description = "공식 PDF 전자투표 찬반 명단 기반 Vertex AI Search 문서");

-- Non-destructive migration for tables created before source/canonical IDs were split.
ALTER TABLE `proj-aj04-211200020328.assembly.meetings`
  ADD COLUMN IF NOT EXISTS raw_html_gcs_uri STRING;
ALTER TABLE `proj-aj04-211200020328.assembly.meetings`
  ADD COLUMN IF NOT EXISTS raw_pdf_gcs_uri STRING;
ALTER TABLE `proj-aj04-211200020328.assembly.utterances`
  ADD COLUMN IF NOT EXISTS source_speaker_id STRING;
ALTER TABLE `proj-aj04-211200020328.assembly.utterances`
  ADD COLUMN IF NOT EXISTS legislator_id STRING;

UPDATE `proj-aj04-211200020328.assembly.utterances`
SET source_speaker_id = speaker_member_id
WHERE source_speaker_id IS NULL AND speaker_member_id IS NOT NULL;
