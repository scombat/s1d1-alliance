terraform {
  required_version = ">= 1.9.0"

  # HCP Terraform. The organisation is not written here: it comes from the TF_CLOUD_ORGANIZATION
  # environment variable (and the token from `terraform login` or TF_TOKEN_app_terraform_io).
  cloud {
    workspaces {
      name = "s1d1-alliance"
    }
  }

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "= 5.28.1"
    }
    random = {
      source  = "hashicorp/random"
      version = "= 3.9.1"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
