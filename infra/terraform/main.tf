terraform {
  required_version = ">= 1.14.0"

  backend "local" {}

  required_providers {
    neon = {
      source  = "kislerdm/neon"
      version = "~> 0.18.0"
    }
    vercel = {
      source  = "vercel/vercel"
      version = "~> 5.24.0"
    }
  }
}

provider "neon" {}

provider "vercel" {}

resource "vercel_project" "portfolio" {
  name                       = var.vercel_project_name
  framework                  = "fastapi"
  serverless_function_region = var.vercel_function_region
}

resource "neon_project" "portfolio" {
  name      = var.neon_project_name
  region_id = var.neon_region_id
  quota {
    active_time_seconds = 1440000
    logical_size_bytes  = 1073741824
  }

  primary_compute {
    autoscaling_limit_min_cu = 0.25
    autoscaling_limit_max_cu = 0.25
    suspend_timeout_seconds  = 300
  }
}
