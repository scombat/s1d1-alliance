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
    google = {
      source  = "hashicorp/google"
      version = "= 8.8.1"
    }
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "= 1.70.1"
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

# Google Cloud: the project and region come from variables. Credentials come from the environment of the HCP
# workspace (GOOGLE_CREDENTIALS, or dynamic provider credentials), never from the code.
# user_project_override + billing_project: the Billing Budgets API refuses calls that carry no quota project.
provider "google" {
  project               = var.gcp_project
  region                = var.gcp_region
  user_project_override = true
  billing_project       = var.gcp_project
}

# Hetzner Cloud: the token is a variable (sensitive) of the HCP workspace, never read from the environment of a laptop.
provider "hcloud" {
  token = var.hcloud_token
}
