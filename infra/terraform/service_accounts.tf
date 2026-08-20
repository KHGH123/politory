resource "google_service_account" "cloud_build" {
  project      = var.project_id
  account_id   = "uijeonggirok-cb"
  display_name = "Cloud Build service account"
}

resource "google_service_account" "backend_app" {
  project      = var.project_id
  account_id   = "uijeonggirok-app"
  display_name = "Cloud Run backend service account"
}
