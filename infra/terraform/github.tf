# GitHub 저장소를 Cloud Build 2세대 연결에 붙인다.
# my-github-connection은 콘솔에서 "호스트 연결 만들기"로 먼저 수동 생성 필요 (Day 1 CI/CD 랩 참고).

resource "google_cloudbuildv2_repository" "backend_repo" {
  project           = var.project_id
  location          = var.region
  name              = var.repository_name
  parent_connection = var.host_connection_name
  remote_uri        = "https://github.com/${var.repository_owner}/${var.repository_name}.git"
}
