# 자리표시자 Cloud Run 서비스. CI/CD 파이프라인이 실행되면 실제 이미지로 교체 배포됨.
# politory 서비스 하나가 프론트+백엔드를 함께 서빙한다(backend/main.py가
# frontend 빌드 결과인 backend/static/을 같은 오리진으로 서빙 — CORS 설정도
# 그래서 불필요, config.py의 기본값(로컬 개발용)만으로 충분하다).

resource "google_cloud_run_v2_service" "backend" {
  project  = var.project_id
  name     = var.service_name
  location = var.region

  deletion_protection = false

  template {
    service_account = google_service_account.backend_app.email
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      # Vertex AI 경로로 Gemini 호출(API 키 대신 backend_app 서비스계정의
      # roles/aiplatform.user로 인증 — GOOGLE_APPLICATION_CREDENTIALS는
      # Cloud Run에서 ADC가 자동으로 처리하므로 불필요, .env의 로컬 전용 값).
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }
      env {
        name  = "MODEL"
        value = "gemini-3.5-flash"
      }
      # assembly 데이터셋이 실제로 있는 프로젝트가 GOOGLE_CLOUD_PROJECT와 달라
      # BigQuery 조회 전용으로 분리 (config.py의 bigquery_project 프로퍼티 참고).
      env {
        name  = "BIGQUERY_PROJECT"
        value = "proj-aj04-211200020328"
      }
      env {
        name  = "BIGQUERY_DATASET"
        value = "assembly"
      }

      # NAVER API HUB 뉴스 검색 인증 — Secret Manager 참조(secrets.tf).
      # env 이름에 하이픈을 쓰면 안 된다 — 처음엔 네이버 공식 헤더명 그대로
      # (X-NCP-APIGW-API-KEY-ID 등) 썼는데, Cloud Run이 하이픈 포함 env
      # 이름을 컨테이너 프로세스에 아예 주입하지 않는다는 걸 실측으로 확인
      # (같은 시크릿을 하이픈 없는 임시 이름으로 참조하면 정상 로딩됨 —
      # /debug/env-check로 os.environ 직접 대조). 그 결과 배포 환경에서
      # NAVER_CLIENT_ID/SECRET이 항상 빈 문자열이 되어 뉴스 검색이 매번
      # 빈 결과를 반환하고, context_agent가 "정보 없음"만 응답하는 게
      # 실제 배포 장애였다. config.py도 이 이름 그대로 읽도록 맞춰뒀다.
      env {
        name = "NAVER_CLIENT_ID"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.naver_client_id.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "NAVER_CLIENT_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.naver_client_secret.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [
    google_project_service.services,
    google_secret_manager_secret_iam_member.backend_naver_client_id_accessor,
    google_secret_manager_secret_iam_member.backend_naver_client_secret_accessor,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public_access" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
