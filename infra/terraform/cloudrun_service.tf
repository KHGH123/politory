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
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_project_service.services]
}

resource "google_cloud_run_v2_service_iam_member" "public_access" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.backend.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
