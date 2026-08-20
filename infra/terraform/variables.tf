variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "asia-northeast3"
}

variable "service_name" {
  type    = string
  default = "uijeonggirok-backend"
}

variable "repository_name" {
  description = "GitHub 저장소 이름"
  type        = string
}

variable "repository_owner" {
  description = "GitHub 저장소 소유자(계정/조직)"
  type        = string
}

variable "host_connection_name" {
  type    = string
  default = "uijeonggirok-github-connection"
}
