# 자리표시자 Cloud Run 서비스. CI/CD 파이프라인이 실행되면 실제 이미지로 교체 배포됨.

resource "google_cloud_run_v2_service" "backend" {
  project  = var.project_id
  name     = var.service_name
  location = var.region

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
