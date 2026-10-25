# terraform

Infrastructure of the public instance `s1d1.qoo.gy`, in HCP Terraform (workspace `s1d1-alliance`).
This folder holds the Cloudflare part (tunnel, DNS, Workers, routes, R2) and the Google Cloud part (raw
bucket, service accounts, anonymizer job on Cloud Run and its schedule, budget, CI identity): `gcp.tf`, `budget.tf`, `wif.tf`.

Providers are pinned exactly: `cloudflare/cloudflare = 5.28.1`, `hashicorp/google = 8.8.1`, `hashicorp/random = 3.9.1`, `hetznercloud/hcloud = 1.70.1`
(no `google-beta`: every resource used is GA). The lock file
`.terraform.lock.hcl` is committed with the hashes for `linux_amd64` (CI, HCP) and `darwin_arm64` (laptops).
To add a platform or upgrade: `terraform providers lock -platform=linux_amd64 -platform=darwin_arm64`.

## HCP Terraform workspace

`versions.tf` has `cloud { workspaces { name = "s1d1-alliance" } }`. The organisation is not in the code:

```
export TF_CLOUD_ORGANIZATION=<your organisation>
terraform login            # or TF_TOKEN_app_terraform_io
terraform init
```

**The configuration reads files outside `terraform/`**: the Worker sources (`../workers/site/src`,
`../workers/ingest/src`, `../workers/ingest/vendor`) and the static site (`../site`). So the HCP workspace must be
VCS-driven with the **repository root as VCS root and Terraform Working Directory = `terraform`** (Settings, General,
"Terraform Working Directory"): the whole repository is then checked out for each run. A workspace whose upload
contains only `terraform/` fails at plan with "no such file". For a CLI-driven run, start it **from the repository
root** with the working directory set the same way (`TF_WORKSPACE`/the `cloud` block, then `terraform -chdir=terraform
plan`), so that the CLI uploads the whole repository, not only `terraform/`.

The workspace runs remotely, so the variables below are set on the workspace (sensitive ones marked as such), not in
a file. `*.tfvars` is ignored by git; never commit one.

Checks that need no account (what CI runs):

```
terraform fmt -check -recursive
terraform init -backend=false     # the `cloud` block does not get in the way
terraform validate
```

## Variables

| name | sensitive | default | role |
|---|---|---|---|
| `cloudflare_account_id` | no | none | account that owns the tunnel, the Workers, R2 |
| `cloudflare_zone_id` | no | none | zone `qoo.gy` |
| `cloudflare_api_token` | yes | none | token of the provider (permissions below) |
| `deploy_version` | no | `v1` | `DEPLOY_VERSION`, part of the cache key: bump it to empty the cache |
| `status_commit` | no | `unknown` | `STATUS_COMMIT` of `/status.json` |
| `status_model` | no | `unknown` | `STATUS_MODEL` of `/status.json` |
| `maintenance` | no | `0` | `"1"`: `/api/*` and `/ingest` answer 503, the landing stays up |
| `cache_ttl` | no | `86400` | `CACHE_TTL` in seconds, a whole number greater than 0 |
| `debug_cache_header` | no | `0` | `DEBUG_CACHE_HEADER`: `"1"` adds `x-s1d1-cache: HIT`/`MISS` to cached API answers (smoke tests only) |
| `origin_requires_auth` | no | `0` | `ORIGIN_REQUIRES_AUTH`: `"1"` when the origin gates decisions behind a token; the site Worker then bypasses the cache |
| `gcp_project` | no | none | Google Cloud project id; also names the raw bucket `<project>-s1d1-raw` (bucket names are global) |
| `terraform_service_account` | no | none | email of the account HCP runs as (same as `TFC_GCP_RUN_SERVICE_ACCOUNT_EMAIL`); gets `roles/iam.serviceAccountUser` on `anonymizer` and `anonymizer-scheduler` only |
| `gcp_region` | no | `europe-west1` | bucket, Cloud Run Job, Cloud Scheduler, Artifact Registry, secrets |
| `billing_account` | no | none | billing account id (`000000-000000-000000`) for the budget |
| `budget_amount` | no | `5` | monthly budget, whole units |
| `budget_currency` | no | `EUR` | must be the currency of the billing account |
| `anonymizer_image` | no | null | `<region>-docker.pkg.dev/<project>/s1d1/anonymizer@sha256:<64 hex>`, built by CI from this repo; a tag without a digest is refused. Null: no Cloud Run Job, no schedule, no anonymizer IAM bindings (first apply) |
| `github_repository` | no | `scombat/s1d1-alliance` | the repository whose `main` branch may push images |
| `github_repository_owner_id` | no | empty | optional numeric id of the GitHub owner, added to the federation condition |
| `hcloud_token` | yes | none | API token (Read & Write) of the Hetzner Cloud project that holds the node |
| `hcloud_location` | no | `nbg1` | `fsn1`, `nbg1` or `hel1`: the EU locations only, on purpose (the node stays in the EU) |
| `hcloud_server_type` | no | `cax11` | Arm (Ampere), 2 vCPU, 4 GB RAM, 40 GB disk; x86 fallback `cx23` or `cpx22`: see "Hetzner node" |

The GCS values of the ingest Worker (`RAW_BUCKET`, `GCS_HMAC_ID`, `GCS_HMAC_SECRET`) are not variables: they are
`local.raw_bucket_name`, `local.gcs_hmac_id` and `local.gcs_hmac_secret`, defined in `gcp.tf` from the raw bucket and
the HMAC key of `ingest-writer`. The R2 credentials of the anonymizer are not variables either: the secrets are made
from the R2 API token of `cloudflare.tf`.

## Outputs (all sensitive except `r2_endpoint`)

| name | what |
|---|---|
| `tunnel_token` | for `cloudflared tunnel run --token` on the nodes |
| `community_token` | `S1D1_CAPTURE_TOKEN` of the community node, `COMMUNITY_TOKEN` of the ingest Worker |
| `r2_access_key_id` | S3 access key id of `s1d1-clean`: the id of the API token |
| `r2_secret_access_key` | S3 secret: the SHA-256 of the API token value |
| `r2_endpoint` | `https://<account>.eu.r2.cloudflarestorage.com` (EU jurisdiction: a jurisdictional bucket is only reachable on its own endpoint) |

Four outputs of the Google part are not sensitive: `ci_workload_identity_provider` and `ci_publisher_service_account`
(the two inputs of `google-github-actions/auth`), `artifact_registry_repository` (where to push) and
`anonymizer_job` (the job's name, null until `anonymizer_image` is set). No GCP secret is an output; the HMAC secret only goes to the ingest Worker binding.

`node_ipv4` (not sensitive) is the Hetzner node's IPv4 address; nothing listens on it.

Read one with `terraform output -raw community_token` (or in the workspace page).

## What is created

- Tunnel `s1d1` (remotely managed) and its ingress: `s1d1.qoo.gy`, path
  `^/api/(v1/(chat/completions|models|decisions|systemone(/batch)?)|classify|predict|models)$` to
  `http://tantive:8788`, then `http_status:404`. No path is rewritten: tantive receives `/api/...`. `S1D1_ROOT_PATH=/api`
  alone does not make that work; tantive 0.8.0 and later handle a request path that starts with the prefix (fixed in
  s1d1-tantive PR #5), and smoke test 9.1 of `docs/operations.md` checks `POST /api/v1/systemone` end to end.
- `CNAME s1d1` to `<tunnel id>.cfargotunnel.com`, proxied.
- Workers `s1d1-site` (static files of `../site`, Analytics Engine binding `VISITS` on dataset `s1d1_visits`),
  `s1d1-ingest` and `s1d1-limiter` (no route); routes `s1d1.qoo.gy/*` to the first and `s1d1.qoo.gy/ingest` to the second.
- **No zone ruleset.** The zone `qoo.gy` already has its `http_ratelimit` entrypoint ruleset, which holds the rule of
  another subdomain, and the Free plan allows a single rate limiting rule per zone: creating ours failed with
  error 20217. Terraform never manages that ruleset (never declare a `cloudflare_ruleset`, never import the
  entrypoint), so that rule is never touched. `workers/site/test/terraform.test.js` fails if one appears.
- **Per-client limits: the `Limiter` Durable Object** of `s1d1-limiter` (`workers/limiter`, SQLite-backed class,
  counters in memory only), reached by the `LIMITER` binding of `s1d1-site` (`/api/*`: 30 per 10 s and 1 000 per
  hour, see `workers/site/README.md`) and of `s1d1-ingest` (100 per 10 s and 100 per 60 s, community node exempt, see
  `workers/ingest/README.md`). A client is named by an HMAC of its IP under `LIMIT_SALT` (`random_password.limit_salt`,
  a secret of both Workers, never output). The Workers Rate Limiting bindings used before were measured on 2026-10-25
  not to limit anything (430 requests to `/api` and 130 to `/ingest` from one IP, all accepted) and are gone.
- **Durable Object migrations.** The provider sends the `migrations` block with every upload of `s1d1-limiter`, not
  only the first (5.28.1, `workers_script/resource.go`). The tag `v1` creates the class at the first apply; whether
  Cloudflare accepts `v1` again on a later upload of that script is not verified, so the script is kept tiny and
  separate (uploading the site or ingest Worker never sends a migration). If such an upload is refused on the
  migration tag, add `old_tag = "v1"` to the block.
- Workers Logs are pinned in `workers.tf` (`observability`) on the three Workers: logs and persistence on, head sampling 1,
  traces off, **invocation logs off on purpose** (they store request and client details at Cloudflare, against the
  privacy policy: no IP or headers recorded). Never enable them from the dashboard: the next apply reverts it.
  `workers/ingest/test/terraform.test.js` fails if either Worker stops declaring `invocation_logs = false`.
  The block also writes out the values the API fills in by itself (`destinations = []` for logs and traces, traces
  head sampling 1 and persistence): the provider copies them into the state on refresh, so a value left unset shows
  as an in-place update of the Worker in every plan. The same test fails if one of them is removed.
- R2 bucket `s1d1-clean` (jurisdiction `eu`) and an API token limited to it, with the one permission group
  `Workers R2 Storage Bucket Item Write`: the anonymizer only writes (`PutObject`), so `Item Read` is not granted. R2
  has no write-only object permission: `Item Write` is the dashboard's "Object Read & Write" and also allows reading
  and listing that bucket's objects (https://developers.cloudflare.com/r2/api/tokens/). The bucket scope is the limit.
- `random_password.community_token` (48 characters, no special character).
- `cloudflare_workers_script_subdomain` with `enabled = false` for both Workers, so that they are served by the
  routes only (a workers.dev copy of the site Worker would be reached without the hostname's route). If the apply
  fails on these two resources because the account has no workers.dev subdomain registered, either register one
  once (dashboard: Workers & Pages, "Your subdomain"; nothing is exposed since the Workers are then disabled on
  it) and re-run, or remove the two `cloudflare_workers_script_subdomain` blocks from `workers.tf` and check by hand
  that `s1d1-site.<subdomain>.workers.dev` does not answer.

A change to any Worker file or to `../site` redeploys: the provider hashes at plan time each `files` entry
(`files.<name>.content_sha256`, computed from `content_file`) and the whole asset folder
(`assets.asset_manifest_sha256`, from `assets.directory`), and the main module through `content_sha256`, so editing
`api.js`, `cache.js`, `gcs.js`, `validate.js`, `vendor/aws4fetch.js` or any site file shows as a diff. Nothing else
is needed (see the comment in `workers.tf`).

How the Workers are uploaded: no build and no bundler. `content_file` is the main module (`src/index.js`) and the
other modules are `files` entries named by their path (`src/api.js`, `vendor/aws4fetch.js`, ...), so the relative
imports resolve inside the upload as they do on disk. The static assets are uploaded from `../site` by the
provider (`assets.directory`); `run_worker_first = ["/", "/status.json", "/api/*"]`.

## What is created on Google Cloud

```
ingest Worker --HMAC--> raw bucket <--reads-- anonymizer job --S3--> R2 s1d1-clean
                                                  ^ every 2 days: Cloud Scheduler --OAuth--> jobs.run
```

- **APIs** (`google_project_service`, never disabled on destroy): run, cloudscheduler, storage, secretmanager,
  artifactregistry, iam, iamcredentials, sts, billingbudgets, cloudresourcemanager (`iam` is not in the brief but the
  service accounts need it). eventarc and pubsub left the list with the Eventarc trigger (2026-10-25); leaving the
  list does not disable them.
- **Raw bucket** `<project>-s1d1-raw`, region `europe-west1`, uniform bucket-level access, public access prevention
  `enforced`, objects deleted at age 3 days. The soft-delete window is set to 0: the default 7 days would keep the
  deleted raw prompts recoverable for a week more.
- **Service accounts**, one role each, all on the bucket, secret or job concerned, none at project level:
  - `ingest-writer`: `roles/storage.objectCreator` on the raw bucket only; an HMAC key for the Worker. It can neither
    read, list, overwrite nor delete.
  - `anonymizer`: `roles/storage.objectViewer` on the raw bucket only (read and list, never delete: the lifecycle
    rule deletes), `roles/secretmanager.secretAccessor` on the two R2 secrets only; it is the identity of the job.
  - `anonymizer-scheduler`: `roles/run.invoker` on the anonymizer job only (it holds `run.jobs.run`); it is the
    identity of the Cloud Scheduler job.
  - `ci-publisher`: `roles/artifactregistry.writer` on the `s1d1` repository only (see below).
- **Secrets** `r2-access-key-id`, `r2-secret-access-key` in Secret Manager, replicated in `europe-west1` only. The
  job reads the pinned version, so a rotation (new R2 token) changes the job.
- **Artifact Registry** `s1d1` (Docker), with **no cleanup policy**: a DELETE rule by age would delete the deployed
  image when no new one was pushed for that long (the job could then not start), and a KEEP rule alone deletes
  nothing. Delete old images by hand (`gcloud artifacts docker images list` / `delete`) when the storage cost
  matters, never the digest in `terraform/images.auto.tfvars`.
- **Created only when `anonymizer_image` is set** (the second apply of `docs/operations.md`, section 3): the job, its
  `run.invoker` binding, the Cloud Scheduler job and the anonymizer's bucket and secret bindings.
- **Cloud Run Job** `s1d1-anonymizer-job` (`python -m anonymizer.batch`, `anonymizer/README.md`): one task, no
  retry, timeout 3600 s, 2 vCPU, **8 GiB** (measured peak 4.1 to 4.6 GiB on 2026-10-25; Cloud Run needs 2 vCPU above
  4 GiB). Environment: `RAW_BUCKET`, `MAX_PROMPTS` (`anonymizer_max_prompts`), `MAX_SECONDS=3300`, `R2_ENDPOINT`,
  `R2_BUCKET=s1d1-clean`, `GLINER_MODEL_DIRS` (the image default, spelled out), `GLINER_THRESHOLD=0.5`;
  `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY` from Secret Manager. A job has no URL.
- **Cloud Scheduler** `s1d1-anonymizer-every-2-days`: `anonymizer_schedule`, `0 3 */2 * *` UTC by default, the odd
  days of the month, so two runs are never more than 2 days apart and every raw object is seen by at least one run
  before its 3-day deletion. `POST https://run.googleapis.com/v2/projects/<project>/locations/<region>/jobs/s1d1-anonymizer-job:run`
  with an OAuth token of `anonymizer-scheduler`, no retry.
- **Cost bound**: one run every 2 days, each at most `anonymizer_max_prompts` lines and one hour of 2 vCPU and 8 GiB,
  whatever the traffic (docs/31 of the project). The lines not drawn are deleted unread at 3 days.
- **Budget** (`budget.tf`): 5 EUR a month on this project, alerts at 50, 90 and 100 % of the current spend, emailed to
  the billing account administrators and users (Cloud Billing's default recipients). It alerts and never stops anything.
- **Workload Identity Federation** (`wif.tf`): pool `github`, OIDC provider `github-main` (issuer
  `https://token.actions.githubusercontent.com`). The condition is
  `assertion.repository == "<github_repository>" && assertion.ref == "refs/heads/main"`, plus the numeric owner id when
  `github_repository_owner_id` is set (GitHub advises it: a name can be released and re-registered by someone else).
  `ci-publisher` can be impersonated by `principalSet .../attribute.repository/<github_repository>` only.

### Leaving Eventarc (2026-10-25)

The apply that brings the job destroys the Cloud Run service `s1d1-anonymizer`, its invoker binding, the Eventarc
trigger `s1d1-raw-finalized`, the `eventarc-invoker` account and the grants made for it: `roles/eventarc.eventReceiver`,
`roles/eventarc.serviceAgent` of the Eventarc agent, `roles/pubsub.publisher` of the Cloud Storage agent and the
Pub/Sub agent's token creator on `eventarc-invoker`. If a project-level `roles/iam.serviceAccountTokenCreator` made by
hand at the first deployment is still there for the Pub/Sub agent, remove it
(`gcloud projects remove-iam-policy-binding … --role=roles/iam.serviceAccountTokenCreator`). The raw objects already
cleaned by the service have their clean object in R2: the job leaves them out, so nothing is cleaned twice.

## Token permissions (`cloudflare_api_token`)

Create the provider token in the dashboard with the permissions that the provider documents for each resource:

- Account: Workers Scripts Write, Workers R2 Storage Write, Cloudflare Tunnel Write (Connectors).
- User: **API Tokens Write** (the dashboard shows it as User, API Tokens, Edit). `cloudflare_api_token` creates a
  user-owned token (`/user/tokens`) and `cloudflare_api_token_permission_groups_list` reads
  `/user/tokens/permission_groups`; the provider documents "API Tokens Write" (or Read, for the list) for both. Cloudflare
  offers this permission only through the "Create additional tokens" template, not in the custom token builder: start
  the provider token from that template and add the other permissions to it. A token cannot grant more than its
  creator holds, so the provider token also needs the R2 write permission above.
- Zone `qoo.gy`: Workers Routes Write, DNS Write. No ruleset (Zone WAF / rate limiting) permission is needed: Terraform
  does not manage any zone ruleset (see above).

## Hetzner node (`hetzner.tf`)

One `hcloud_server` of type `cax11` (Arm / Ampere, 2 vCPU, 4 GB RAM, 40 GB disk; about 6 EUR a month excluding VAT, paid
by the Hetzner project, which has its own billing: the Google Cloud budget does not cover it) in `nbg1`, on the image
`ubuntu-24.04`. Hetzner images are published for both architectures under the same name; the provider asks for the one
that matches the server type (`GetForArchitecture` in `internal/server/resource.go` of hcloud 1.70.1), so the node gets
the Arm build. The server has a public IPv4 and a public IPv6 address (ghcr.io and github.com are not all reachable
over IPv6 only). The cloud-init is `../node/cloud-init.yaml.tftpl`, filled with the tunnel token, the community token
and the URL of `node/desired.env` on `main`. Server and firewall carry the labels `project=s1d1-alliance`,
`role=node`.

- **No inbound access at all.** `hcloud_firewall.node` has no `rule` block and is attached to the server with
  `firewall_ids`. Hetzner's documentation: "If you do not set any rule, all inbound traffic will automatically be
  blocked" and all outbound traffic is permitted
  (<https://docs.hetzner.com/cloud/firewalls/overview/>). The public addresses are there only so the node can go out
  (tunnel, ghcr.io, raw.githubusercontent.com, apt). The compose publishes no port.
- **No SSH key, no password login.** Without `ssh_keys`, Hetzner e-mails a root password. The cloud-init sets
  `ssh_pwauth: false` and stops and masks `ssh.service` and `ssh.socket`, so that password is of no use over the
  network; it still opens a session on the web console. Change that password at the first
  console login or reset it from the Console. To look inside the node, use the
  Hetzner Console (the server, "Console": web VNC) with that password. A node you cannot repair is
  replaced (`terraform apply -replace=hcloud_server.node`): it holds no state that matters (the capture folder is kept
  two days).
- **No drift on a running node.** `lifecycle { ignore_changes = [user_data, image] }`: a newer Ubuntu image or an edited
  cloud-init does not replace the server. The node follows `desired.env` by itself.
- **Arm capacity.** The apply can fail on the server when Hetzner has no CAX capacity in the location (the product page
  sometimes shows the CAX plans as unavailable). The firewall is created anyway; set `hcloud_location` to another of
  the three EU locations and apply again, or retry later. If CAX (Arm) stays unavailable, set `hcloud_server_type` to a shared x86 type with 4 GB of RAM: `cx23`
  (2 vCPU, 40 GB, Cost-Optimized; Hetzner's current smallest x86 type, successor of `cx22`) or `cpx22` (2 AMD vCPU, 80 GB,
  Regular Performance). Hetzner's product pages list both for `nbg1` and `hel1` (not `fsn1`); check the Console for
  the location you choose. Nothing else changes: the image follows the architecture of the type, and the cloud-init
  carries the cosign checksums for aarch64 and x86_64.
- **API token.** In the Hetzner Console, open the project, Security, API tokens, "Generate API token" with the
  permission **Read & Write**, and put it in the sensitive workspace variable `hcloud_token`. A token belongs to one
  project: the node, its firewall and its IPs live in that project.

## Manual steps (Terraform cannot do them)

1. **Route failure mode (fail open / fail closed).** `cloudflare_workers_route` (5.28.1) has only `zone_id`,
   `pattern` and `script`. The setting exists only in the dashboard: Workers & Pages, the Worker, Settings, Domains
   & Routes, the route, "Failure mode". Fail open bypasses the Worker when it cannot run, so `/api/*` would go
   straight to the tunnel without the allow-list, the cache and the maintenance switch; fail closed returns a
   Cloudflare 1027 page. For `s1d1.qoo.gy/*` choose **fail open** (docs/26
   decision 22): it is safe because the tunnel ingress still lists only the API paths and answers 404 for the rest
   (`/metrics` included), and what is lost when the Worker cannot run is the cache, the maintenance switch and the
   landing, which is accepted. For `s1d1.qoo.gy/ingest` choose **fail closed**, so that ingestion never reaches a
   tunnel that does not serve it. Check both values after each apply (open question 4 of docs/26).
2. **Tunnel connector.** The tunnel exists after the apply, with no connector: the nodes start `cloudflared` with
   `terraform output -raw tunnel_token`.
3. **Google Cloud bootstrap, by hand, once.** A project with billing linked, `serviceusage.googleapis.com` and
   `cloudresourcemanager.googleapis.com` enabled (Terraform enables the other APIs but needs these two to do it), and a
   service account for HCP with: Project IAM
   Admin, Service Usage Admin, Storage Admin, **Storage HMAC Key Admin (`roles/storage.hmacKeyAdmin`)** (Storage Admin
   does not grant `storage.hmacKeys.create`: the first apply failed on the HMAC key without it), Service Account Admin,
   Cloud Run Admin, **Cloud Scheduler Admin (`roles/cloudscheduler.admin`)**, Secret Manager Admin, Artifact Registry
   Administrator, Workload Identity Pool Admin, `iam.serviceAccountUser` (at the project level for the first apply only:
   Terraform then grants it on `anonymizer` and `anonymizer-scheduler` and you remove the project-level one, see
   below), and Billing Account Costs Manager
   (or Billing Account Administrator) **on the billing account** for the budget. This account is the most powerful
   credential of the project: keep it out of the repository and rotate it.
   An install made before 2026-10-25 has Eventarc Admin and Pub/Sub Admin instead of Cloud Scheduler Admin: add Cloud
   Scheduler Admin **before** the apply that brings the job, and remove the two others only **after** it (that apply
   still needs them to destroy the Eventarc trigger).

   Recommended way to give HCP that account: **dynamic provider credentials** (workload identity, no key to store). In
   a workload identity pool `hcp-terraform` (OIDC provider for `https://app.terraform.io`) with an attribute condition
   limited to organization `scombat` and workspace `s1d1-alliance`, let that pool impersonate the service account
   (`roles/iam.workloadIdentityUser`), then set these environment variables on the workspace:
   `TFC_GCP_PROVIDER_AUTH` = `true`, `TFC_GCP_RUN_SERVICE_ACCOUNT_EMAIL` (the service account) and
   `TFC_GCP_WORKLOAD_PROVIDER_NAME` (`projects/<number>/locations/global/workloadIdentityPools/hcp-terraform/providers/<provider>`).
   The alternative is a service account key in the sensitive environment variable `GOOGLE_CREDENTIALS`, which is a
   long-lived secret.

   Set the workspace variable `terraform_service_account` (non-sensitive) to the same email. The bootstrap grants
   `roles/iam.serviceAccountUser` on the whole project because the runtime accounts do not exist yet; the first apply
   with `google_service_account_iam_member.terraform_acts_as_*` creates the narrow grants. **Only after that apply
   succeeded**, remove the project-level one by hand:

   ```sh
   gcloud projects remove-iam-policy-binding <project> --member="serviceAccount:<hcp sa>" \
     --role="roles/iam.serviceAccountUser" --condition=None
   ```

   `/status.json` takes its Tantive image from the `TANTIVE_IMAGE` line of `node/desired.env` (no variable any more):
   delete the `tantive_image` workspace variable in HCP after merge (an undeclared variable only gives a warning, but
   keep the workspace clean).
4. **First image.** The image must exist in Artifact Registry before the Cloud Run Job is created, so the first
   apply runs with `anonymizer_image` null (its default) and creates no anonymizer; the CI then pushes the image
   (`workflow_dispatch`), a pull request adds `terraform/images.auto.tfvars` with its digest, and the next apply
   creates the anonymizer. No `-target` is needed (the workspace is VCS-driven). Details: `docs/operations.md`,
   section 3.
5. **Currency.** `budget_currency` must equal the billing account's currency; the API rejects the budget otherwise.
6. **Account.** R2 must be enabled on the account before the first apply, and so must **Analytics Engine** (dashboard:
   Workers & Pages, Analytics Engine, enable once): without it the `VISITS` binding of the site Worker fails the
   apply with error 10089.

## Cloudflare behaviours that matter here

- **The Worker's `ORIGIN` is `https://s1d1.qoo.gy` and it does not loop.** Cloudflare: "Routes cannot be the target
  of a same-zone `fetch()` call", and `fetch()` from a route Worker goes to the application server defined in the
  zone's DNS (here the tunnel CNAME), see https://developers.cloudflare.com/workers/configuration/routing/routes/.
  This holds for **routes**; a Custom Domain on the same hostname would invoke the Worker again, so never add one.
- **Rate limit in the Worker, not in the zone.** The Free plan allows one rate limiting rule per zone and `qoo.gy`'s
  is used by another subdomain, so `/api/*` is limited by the site Worker, which asks the `Limiter` Durable Object
  once per request (the Worker's own subrequest to the origin is not counted). One Durable Object request per API
  call: the Free plan's 100 000 a day match the Workers' own daily quota. Trade-off: when the Workers daily request
  quota is exhausted the route fails open (manual step 1), `/api/*` goes straight to the tunnel and has no rate limit;
  the tunnel ingress still allows the API paths only and the deadline shedding of tantive still protects its
  capacity. It costs nothing: the Free plan bills nothing over its quota and the origin is a fixed-price node.
