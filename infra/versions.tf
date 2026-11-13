terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.40"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6"
    }
  }

  backend "gcs" {
    bucket = "khamsebot-folio-tfstate"
    prefix = "folio"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
