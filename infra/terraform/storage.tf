resource "google_artifact_registry_repository" "backend_repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "uijeonggirok-repo"
  format        = "DOCKER"

  depends_on = [google_project_service.services]
}
