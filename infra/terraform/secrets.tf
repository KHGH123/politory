# 네이버 뉴스 검색 API(agent/tools/web_search_tool.py) 인증 키. Terraform이
# 값 자체는 관리하지 않는다(gcloud secrets versions add로 별도 관리 —
# .tfvars/.tfstate에 평문 API 키가 남는 걸 피하기 위함) — 여기서는 시크릿
# 리소스 존재와 Cloud Run 서비스계정의 접근 권한만 선언한다.
resource "google_secret_manager_secret" "naver_client_id" {
  project   = var.project_id
  secret_id = "naver-client-id"

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret" "naver_client_secret" {
  project   = var.project_id
  secret_id = "naver-client-secret"

  replication {
    auto {}
  }

  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_iam_member" "backend_naver_client_id_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.naver_client_id.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_app.email}"
}

resource "google_secret_manager_secret_iam_member" "backend_naver_client_secret_accessor" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.naver_client_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.backend_app.email}"
}
