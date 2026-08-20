resource "google_project_iam_member" "cloud_build_run_admin" {
  project = var.project_id
  role    = "roles/run.admin"
  member  = "serviceAccount:${google_service_account.cloud_build.email}"
}

resource "google_project_iam_member" "cloud_build_sa_user" {
  project = var.project_id
  role    = "roles/iam.serviceAccountUser"
  member  = "serviceAccount:${google_service_account.cloud_build.email}"
}

# 빌드 로그를 REGIONAL_USER_OWNED_BUCKET(cloudbuild.yaml options)에 쓰기 위해 필요.
resource "google_project_iam_member" "cloud_build_storage_admin" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = "serviceAccount:${google_service_account.cloud_build.email}"
}

# Artifact Registry에 이미지 push하기 위해 필요.
resource "google_project_iam_member" "cloud_build_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.cloud_build.email}"
}

resource "google_project_iam_member" "backend_app_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.backend_app.email}"
}
