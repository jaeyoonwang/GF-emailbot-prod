variable "domain_acc" {
  type        = string
  description = "The application's acceptance domain."
}

variable "azure_client_id" {
  type        = string
  description = "Azure app registration client ID."
}

variable "azure_client_secret" {
  type        = string
  description = "Azure app registration client secret."
}

variable "azure_tenant_id" {
  type        = string
  description = "Gates Foundation Azure tenant ID."
}

variable "anthropic_api_key" {
  type        = string
  description = "Anthropic API key for Claude."
}

variable "session_secret_key" {
  type        = string
  description = "Encryption key for session cookies (32+ chars)."
}

job "__REPO__NAME__-acc" {
  region      = "us-west-2"
  datacenters = ["dc1"]
  type        = "service"
  namespace   = "__NAMESPACE__"

  constraint {
    attribute = attr.kernel.name
    value     = "linux"
  }

  constraint {
    attribute = node.class
    value     = "spot"
  }

  update {
    max_parallel     = 1
    health_check     = "checks"
    min_healthy_time = "30s"
    healthy_deadline = "5m"
    auto_revert      = true
  }

  group "__REPO__NAME__-acc" {
    count = 1

    network {
      port "http" { to = __PORT__NUMBER__ }
    }

    service {
      name     = "__REPO__NAME__-acc"
      port     = "http"
      provider = "nomad"

      check {
        type     = "http"
        path     = "/health"
        interval = "10s"
        timeout  = "3s"
      }

      check {
        type     = "http"
        path     = "/ready"
        interval = "30s"
        timeout  = "5s"
      }

      tags = [
        "traefik.enable=true",
        "traefik.http.routers.__REPO__NAME___acc.rule=Host(`${var.domain_acc}`)",
        "traefik.http.routers.__REPO__NAME___acc.entrypoints=https",
        "traefik.http.routers.__REPO__NAME___acc.tls=true",
        "traefik.http.services.__REPO__NAME___acc.loadbalancer.sticky=true",
        "traefik.http.services.__REPO__NAME___acc.loadbalancer.sticky.cookie.secure=true",
        "traefik.http.services.__REPO__NAME___acc.loadbalancer.sticky.cookie.httpOnly=true"
      ]
    }

    task "__REPO__NAME__-acc" {
      driver = "docker"

      config {
        image           = "bmgfsre.azurecr.io/__REPO__NAME__:__BUILD__NUMBER__"
        ports           = ["http"]
        readonly_rootfs = true
        tmpfs           = ["/tmp:size=64m,noexec,nosuid"]
      }

      env {
        # App config
        APP_ENV            = "staging"
        APP_BASE_URL       = "https://${var.domain_acc}"
        AZURE_REDIRECT_URI = "https://${var.domain_acc}/auth/callback"
        LOG_LEVEL          = "info"

        # Secrets — injected via Nomad variables set by Drone CI
        AZURE_CLIENT_ID     = var.azure_client_id
        AZURE_CLIENT_SECRET = var.azure_client_secret
        AZURE_TENANT_ID     = var.azure_tenant_id
        ANTHROPIC_API_KEY   = var.anthropic_api_key
        SESSION_SECRET_KEY  = var.session_secret_key
      }

      resources {
        cpu    = 500
        memory = 512
      }
    }
  }
}
