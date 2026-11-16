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

# The Google Drive pieces below are public values. The client secret is a Secret Manager secret.
variable "drive_client_id" {
  type    = string
  default = ""
}

variable "drive_picker_api_key" {
  type    = string
  default = ""
}

variable "drive_app_id" {
  type    = string
  default = ""
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

# Same placeholder idea as api_image, for the image that has Chrome.
variable "research_image" {
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/hello"
}
