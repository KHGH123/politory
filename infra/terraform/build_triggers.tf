resource "google_cloudbuild_trigger" "deploy_backend" {
  project  = var.project_id
  location = var.region
  name     = "deploy-politory-backend"

  repository_event_config {
    repository = google_cloudbuildv2_repository.backend_repo.id
    push {
      branch = "^main$"
    }
  }

  filename        = "cloudbuild.yaml"
  service_account = google_service_account.cloud_build.id

  substitutions = {
    _REGION                      = var.region
    _ARTIFACT_REGISTRY_REPO_NAME = google_artifact_registry_repository.backend_repo.repository_id
    _CONTAINER_NAME              = "backend"
    _SERVICE_NAME                = var.service_name
  }
}
