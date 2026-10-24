# s1d1-alliance

The free community instance of the s1d1 prompt router, at <https://s1d1.qoo.gy>.

**This repository is everything that runs s1d1.qoo.gy:** the landing page, the `api` and `ingest` Workers, the
anonymiser, the Terraform for Cloudflare, Google Cloud and Oracle Cloud, and the Compose file of a community node.
Nothing runs there that is not in this repository, and nothing in this repository is a secret.

**Status:** under construction. This commit is the skeleton, the ingestion contract and the secret guard.

**Licence:** to come. There is no `LICENSE` file yet; the licence is the maintainer's decision.

## The data path

```
                           s1d1.qoo.gy  (Cloudflare zone)
          ┌────────────────────────┼──────────────────────────────┐
          │ /                      │ /api/*  (100 req/10 s per IP) │ /ingest
          ▼                        ▼                              ▼
     static landing           Worker "api"                   Worker "ingest"
     + /status.json           closed paths, cache,           bounded batches,
                              SHA-256 of the body,           fail closed
                              fail open                            │
                                   │ miss                          │
                                   ▼                               ▼
                           Cloudflare Tunnel              GCS "raw" (EU, 3 days)
                         ┌─────────┴─────────┐                     │ object created
                         ▼                   ▼                     ▼ Eventarc
                  Oracle A1: tantive   homelab: tantive    Cloud Run: GLiNER + regex
                         └──── captures ─────┘                     │
                               sent to /ingest                     ▼
                                                          R2 "clean" (EU, kept)
   other instances (when enabled) ── batches ──► /ingest
   direct senders (v1 format)     ── batches ──► /ingest
```

- The landing is static. `/status.json` says what runs; `/llms.txt` is the guide for agents.
- `/api/*` accepts a closed list of paths only. Anything else answers 404.
- Only the paths of the API leave the Cloudflare Tunnel; the nodes publish no port.
- A prompt goes to the raw bucket for 3 days, then is anonymised into the clean bucket. The clean copy keeps no
  instance identifier, no IP address and no headers. The contract is in `contract/ingest-v1.md`.
- Each node pulls its own images by digest and checks their cosign signature before it starts them.

## Manual prerequisites

These are done by hand, once, by the maintainer. No secret is stored in this repository.

1. A **Cloudflare** account with the zone `qoo.gy`.
2. A **Google Cloud** project with billing enabled.
3. A **HCP Terraform** organisation, which runs the Terraform in this repository.
4. An **Oracle Cloud** account whose home region is in the EU.

Tokens, keys, OCIDs and `terraform.tfvars` are given to HCP Terraform or to the nodes, never committed.

## Secrets

- `gitleaks` runs as a pre-commit hook and in the CI `secrets` job of `.github/workflows/ci.yml`. It is the installed binary, version 8.30.1.
- Install the hook once with `uvx pre-commit install`. Check the whole tree with `uvx pre-commit run --all-files`.
- `.gitignore` keeps `*.tfvars`, `*.env` and `.terraform/` out of the repository, except the two node templates.

## Contents

- `contract/` the ingestion contract (v1) and its fixtures.
- `.gitleaks.toml`, `.pre-commit-config.yaml`, `.editorconfig`, `.gitignore`.
