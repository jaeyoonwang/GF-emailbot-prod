variable "domain_prod" {
  type        = string
  description = "The application's production domain."
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

job "__REPO__NAME__" {
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

  # Rolling deploy with auto-rollback
  update {
    max_parallel     = 1
    health_check     = "checks"
    min_healthy_time = "30s"
    healthy_deadline = "5m"
    auto_revert      = true
  }

  group "__REPO__NAME__" {
    # Single instance — in-memory sessions cannot be shared across instances.
    # See documentation before increasing count.
    count = 1

    network {
      port "http" { to = __PORT__NUMBER__ }
    }

    service {
      name     = "__REPO__NAME__"
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
        "traefik.http.routers.__REPO__NAME__.rule=Host(`${var.domain_prod}`)",
        "traefik.http.routers.__REPO__NAME__.entrypoints=https",
        "traefik.http.routers.__REPO__NAME__.tls=true",
        "traefik.http.services.__REPO__NAME__.loadbalancer.sticky=true",
        "traefik.http.services.__REPO__NAME__.loadbalancer.sticky.cookie.secure=true",
        "traefik.http.services.__REPO__NAME__.loadbalancer.sticky.cookie.httpOnly=true"
      ]
    }

    task "__REPO__NAME__" {
      driver = "docker"

      config {
        image           = "bmgfsre.azurecr.io/__REPO__NAME__:__BUILD__NUMBER__"
        ports           = ["http"]
        readonly_rootfs = true
        tmpfs           = ["/tmp:size=64m,noexec,nosuid"]
      }

      env {
        # App config
        APP_ENV            = "production"
        APP_BASE_URL       = "https://${var.domain_prod}"
        AZURE_REDIRECT_URI = "https://${var.domain_prod}/auth/callback"
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
