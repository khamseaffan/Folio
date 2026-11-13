variable "project_id" {
  type    = string
  default = "khamsebot"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "frontend_origin" {
  type    = string
  default = "https://folio.khamseaffan.com"
}

variable "github_repo" {
  type    = string
  default = "khamseaffan/Folio"
}

# Placeholder until the first real image is pushed. Deploys change the image outside Terraform.
variable "api_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}
